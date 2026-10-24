"""File management routes for browsing, editing, and deleting songs."""

import json
import logging
import os
import tempfile
import uuid
from urllib.parse import unquote, urlencode

import flask_babel
import gevent
from flask import Blueprint, flash, redirect, render_template, request, url_for, jsonify
from flask_paginate import Pagination, get_page_parameter
from werkzeug.utils import secure_filename

from pikaraoke.lib import llm_renamer, tag_manager, tag_suggester
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin

_ = flask_babel.gettext


files_bp = Blueprint("files", __name__)


@files_bp.route("/browse", methods=["GET"])
def browse():
    """Browse available songs page.
    ---
    tags:
      - Pages
    parameters:
      - name: q
        in: query
        type: string
        description: Search query
      - name: letter
        in: query
        type: string
        description: Filter by first letter (or 'numeric')
      - name: sort
        in: query
        type: string
        description: Sort order ('date' for date, otherwise alphabetical)
    responses:
      200:
        description: HTML browse page
    """
    k = get_karaoke_instance()
    site_name = get_site_name()
    search = False
    q = request.args.get("q")
    if q:
        search = True
    page = request.args.get(get_page_parameter(), type=int, default=1)

    available_songs = k.available_songs

    # Filter by a single user's liked songs
    liked_filter = request.args.get("liked")
    liked_user = request.args.get("liked_user", "").strip() or request.cookies.get("user", "").strip()
    if liked_filter and liked_user:
        liked_set = k.likes_manager.get_liked_songs(liked_user)
        available_songs = [s for s in available_songs if s in liked_set]

    # Filter to the intersection of liked songs for multiple users (match mode)
    match_users_raw = request.args.get("match_users", "").strip()
    match_users = [u.strip() for u in match_users_raw.split(",") if u.strip()] if match_users_raw else []
    if match_users:
        intersection: set[str] = k.likes_manager.get_liked_songs(match_users[0])
        for u in match_users[1:]:
            intersection &= k.likes_manager.get_liked_songs(u)
        available_songs = [s for s in available_songs if s in intersection]

    # Filter by artist (substring match on everything after the first " - " in the title)
    artist_filter = request.args.get("artist", "").strip()
    if artist_filter:
        needle = artist_filter.lower()
        available_songs = [s for s in available_songs if needle in k.filename_from_path(s).lower()]

    # Filter by genre/mood/language/duet tags (stored in each song's own file metadata)
    genre_filter = [g.strip() for g in request.args.get("genre", "").split(",") if g.strip()]
    mood_filter = [m.strip() for m in request.args.get("mood", "").split(",") if m.strip()]
    language_filter = [l.strip() for l in request.args.get("language", "").split(",") if l.strip()]
    # "1" = duets only, "0" = non-duets only, "" = no filter on duet status
    duet_filter = request.args.get("duet", "")

    song_tags_cache: dict[str, dict] = {}
    if genre_filter or mood_filter or language_filter or duet_filter:
        genre_filter_set = {g.lower() for g in genre_filter}
        mood_filter_set = {m.lower() for m in mood_filter}
        language_filter_set = {l.lower() for l in language_filter}
        filtered = []
        for song in available_songs:
            tags = tag_manager.read_tags(song)
            song_tags_cache[song] = tags
            if genre_filter_set and not ({g.lower() for g in tags["genre"]} & genre_filter_set):
                continue
            if mood_filter_set and not ({m.lower() for m in tags["mood"]} & mood_filter_set):
                continue
            if language_filter_set and not ({l.lower() for l in tags["language"]} & language_filter_set):
                continue
            if duet_filter == "1" and not tags["duet"]:
                continue
            if duet_filter == "0" and tags["duet"]:
                continue
            filtered.append(song)
        available_songs = filtered

    letter = request.args.get("letter")

    if letter:
        result = []
        if letter == "numeric":
            for song in available_songs:
                f = k.filename_from_path(song)[0]
                if f.isnumeric():
                    result.append(song)
        else:
            for song in available_songs:
                f = k.filename_from_path(song).lower()
                if f.startswith(letter.lower()):
                    result.append(song)
        available_songs = result

    if "sort" in request.args and request.args["sort"] == "date":
        songs = sorted(available_songs, key=lambda x: os.path.getmtime(x))
        songs.reverse()
        sort_order = "Date"
    else:
        songs = available_songs
        sort_order = "Alphabetical"

    results_per_page = k.browse_results_per_page

    args = request.args.copy()
    args.pop("_", None)

    page_param = get_page_parameter()
    args[page_param] = "{0}"

    args_dict = args.to_dict()
    pagination_href = unquote(url_for("files.browse", **args_dict))  # type: ignore

    # Sort links keep every other active filter, just swap/drop "sort" and reset to page 1.
    sort_args = request.args.copy()
    sort_args.pop("_", None)
    sort_args.pop(page_param, None)
    sort_args.pop("sort", None)
    sort_alpha_url = unquote(url_for("files.browse", **sort_args.to_dict()))
    sort_args["sort"] = "date"
    sort_date_url = unquote(url_for("files.browse", **sort_args.to_dict()))

    # Letter links (A-Z sidebar) keep every other active filter too, just swap "letter".
    letter_base_args = request.args.copy()
    letter_base_args.pop("_", None)
    letter_base_args.pop(page_param, None)
    letter_base_args.pop("letter", None)
    letter_base_qs = urlencode(letter_base_args.to_dict())

    pagination = Pagination(
        css_framework="bulma",
        page=page,
        total=len(songs),
        search=search,
        record_name="songs",
        per_page=results_per_page,
        display_msg="Showing <b>{start} - {end}</b> of <b>{total}</b> {record_name}",
        href=pagination_href,
    )
    start_index = (page - 1) * results_per_page
    page_songs = songs[start_index : start_index + results_per_page]
    song_tags = {s: song_tags_cache.get(s) or tag_manager.read_tags(s) for s in page_songs}

    is_admin_user = is_admin()
    tag_counts = tag_manager.scan_library_vocab_counts(k.available_songs)
    if is_admin_user:
        # Admins manage tags here too, so show every tag (with counts) regardless
        # of tag_min_count -- otherwise a rare/junk tag could never be cleaned up.
        tag_display = {key: sorted(counts.items(), key=lambda kv: kv[0].lower()) for key, counts in tag_counts.items()}
    else:
        min_count = getattr(k, "tag_min_count", 1)
        tag_display = {
            key: sorted(((t, c) for t, c in counts.items() if c >= min_count), key=lambda kv: kv[0].lower())
            for key, counts in tag_counts.items()
        }

    return render_template(
        "files.html",
        pagination=pagination,
        sort_order=sort_order,
        sort_alpha_url=sort_alpha_url,
        sort_date_url=sort_date_url,
        letter_base_qs=letter_base_qs,
        site_title=site_name,
        letter=letter,
        # MSG: Title of the files page.
        title=_("Browse"),
        songs=page_songs,
        admin=is_admin_user,
        match_users=match_users,
        all_liked_users=sorted(k.likes_manager.likes.keys()),
        song_tags=song_tags,
        tag_display=tag_display,
        genre_filter=genre_filter,
        mood_filter=mood_filter,
        language_filter=language_filter,
        duet_filter=duet_filter,
        artist_filter=artist_filter,
    )


@files_bp.route("/files/delete", methods=["GET"])
def delete_file():
    """Delete a song file.
    ---
    tags:
      - Files
    parameters:
      - name: song
        in: query
        type: string
        required: true
        description: Path to the song file to delete
    responses:
      302:
        description: Redirects to browse page
    """
    k = get_karaoke_instance()
    if "song" in request.args:
        song_path = request.args["song"]
        if k.queue_manager.is_song_in_queue(song_path):
            flash(
                # MSG: Message shown after trying to delete a song that is in the queue.
                _("Error: Can't delete this song because it is in the current queue")
                + ": "
                + song_path,
                "is-danger",
            )
        else:
            k.delete(song_path)
            # MSG: Message shown after deleting a song. Followed by the song path
            flash(_("Song deleted: %s") % k.filename_from_path(song_path), "is-warning")
    else:
        # MSG: Message shown after trying to delete a song without specifying the song.
        flash(_("Error: No song specified!"), "is-danger")
    return redirect(url_for("files.browse"))


def _is_safe_song_path(k, song_path: str) -> bool:
    """Validate a song path is a real file within the download directory."""
    if not song_path or not os.path.isfile(song_path):
        return False
    abs_song = os.path.realpath(song_path)
    abs_download = os.path.realpath(k.download_path)
    return abs_song.startswith(abs_download + os.sep) or abs_song == abs_download


def _split_tag_list(raw: str) -> list[str]:
    return [v.strip() for v in (raw or "").split(",") if v.strip()]


def _trigger_tag_backfill(k, song_path: str, before_vocab: dict, applied_tags: dict) -> None:
    """Fire-and-forget: if the saved tags introduced a brand-new value, scan the
    rest of the library for other songs that should get it too.

    Runs as a gevent greenlet (not a native thread) so saving from the edit
    page returns immediately instead of blocking the browser on a
    library-wide LLM pass. This app is gevent-monkeypatched throughout
    (see app.py); spawning a real OS thread that makes its own `requests`
    calls here can contend with gevent's patched urllib3/ssl locks and hang
    indefinitely instead of erroring out, so greenlets are the only safe way
    to do this kind of background I/O in this process.
    """

    def _run():
        try:
            with gevent.Timeout(300, TimeoutError("tag backfill exceeded 5 minutes")):
                origin_title = k.filename_from_path(song_path)
                other_songs = {
                    p: k.filename_from_path(p) for p in k.available_songs if p != song_path
                }
                model = getattr(k, "llm_rename_model", None)
                backfilled = tag_suggester.backfill_new_tags(
                    before_vocab, applied_tags, origin_title, other_songs, model=model
                )
                for (category, value), count in backfilled.items():
                    logging.info(f"Backfilled new tag '{value}' ({category}) to {count} other song(s)")
        except Exception as e:
            logging.warning(f"Tag backfill failed/timed out for '{song_path}': {e}")

    gevent.spawn(_run)


@files_bp.route("/files/edit", methods=["GET", "POST"])
def edit_file():
    k = get_karaoke_instance()
    site_name = get_site_name()
    # MSG: Message shown after trying to edit a song that is in the queue.
    queue_error_msg = _("Error: Can't edit this song because it is in the current queue: ")
    if "song" in request.args:
        song_path = request.args["song"]
        if k.queue_manager.is_song_in_queue(song_path):
            flash(queue_error_msg + song_path, "is-danger")
            return redirect(url_for("files.browse"))
        else:
            return render_template(
                "edit.html",
                site_title=site_name,
                title="Song File Edit",
                song=song_path.encode("utf-8", "ignore"),
                tags=tag_manager.read_tags(song_path),
                tag_vocab=tag_manager.vocab_above_min_count(
                    k.available_songs, getattr(k, "tag_min_count", 1)
                ),
            )
    else:
        d = request.form.to_dict()
        if "old_file_name" in d:
            old_name = d["old_file_name"]
            new_name = d.get("new_file_name", "").strip()
            final_path = old_name

            if k.queue_manager.is_song_in_queue(old_name):
                # check one more time just in case someone added it during editing
                flash(queue_error_msg + old_name, "is-danger")
            else:
                current_name = k.filename_from_path(old_name)
                if new_name and new_name != current_name:
                    # check if new_name already exist
                    file_extension = os.path.splitext(old_name)[1]
                    new_full_path = os.path.join(k.download_path, new_name + file_extension)
                    if os.path.isfile(new_full_path):
                        flash(
                            # MSG: Message shown after trying to rename a file to a name that already exists.
                            _("Error renaming file: '%s' to '%s', Filename already exists")
                            % (old_name, new_name + file_extension),
                            "is-danger",
                        )
                    else:
                        k.rename(old_name, new_name)
                        final_path = new_full_path
                        flash(
                            # MSG: Message shown after renaming a file.
                            _("Renamed file: %s to %s") % (old_name, new_name),
                            "is-warning",
                        )

                # Tags are saved regardless of whether the name changed, so
                # editing just the tags (no rename) works too.
                tags = {
                    "genre": _split_tag_list(d.get("genre", "")),
                    "mood": _split_tag_list(d.get("mood", "")),
                    "language": _split_tag_list(d.get("language", "")),
                    "duet": d.get("duet", "").strip().lower() in ("true", "1", "on", "yes"),
                }
                before_vocab = tag_manager.scan_library_vocab(k.available_songs)
                if tag_manager.write_tags(final_path, tags):
                    _trigger_tag_backfill(k, final_path, before_vocab, tags)
        else:
            # MSG: Message shown after trying to edit a song without specifying the filename.
            flash(_("Error: No filename parameters were specified!"), "is-danger")
        return redirect(url_for("files.browse"))


@files_bp.route("/files/suggest-name", methods=["POST"])
def suggest_name_route():
    """Ask the local LLM for a clean "Title - Artist" suggestion for one song."""
    if not is_admin():
        # MSG: Message shown after trying to use an admin-only AI feature without permissions.
        return jsonify({"success": False, "message": _("You don't have permission to do this")})

    k = get_karaoke_instance()
    song = request.form.get("song", "")

    if not _is_safe_song_path(k, song):
        # MSG: Message shown when a given song path doesn't exist or is invalid.
        return jsonify({"success": False, "message": _("Song not found")})

    raw_name = k.filename_from_path(song)
    model = getattr(k, "llm_rename_model", None)
    suggestion = llm_renamer.suggest_title(raw_name, model=model)

    if not suggestion:
        # MSG: Message shown when the AI couldn't come up with a name suggestion.
        return jsonify({"success": False, "message": _("Could not get a name suggestion")})

    return jsonify({"success": True, "name": suggestion})


@files_bp.route("/files/suggest-tags", methods=["POST"])
def suggest_tags_route():
    """Ask the local LLM for genre/mood/language/duet tags for one song."""
    if not is_admin():
        # MSG: Message shown after trying to use an admin-only AI feature without permissions.
        return jsonify({"success": False, "message": _("You don't have permission to do this")})

    k = get_karaoke_instance()
    song = request.form.get("song", "")

    if not _is_safe_song_path(k, song):
        # MSG: Message shown when a given song path doesn't exist or is invalid.
        return jsonify({"success": False, "message": _("Song not found")})

    vocab = tag_manager.scan_library_vocab(k.available_songs)
    clean_title = k.filename_from_path(song)
    model = getattr(k, "llm_rename_model", None)
    suggestion = tag_suggester.suggest_tags(clean_title, vocab, model=model)

    if not suggestion:
        # MSG: Message shown when the AI couldn't come up with a tag suggestion.
        return jsonify({"success": False, "message": _("Could not get a tag suggestion")})

    return jsonify({"success": True, "tags": suggestion})


@files_bp.route("/files/upload_video", methods=["POST"])
def upload_video():
    """Upload and transcode a video file."""
    k = get_karaoke_instance()
    
    if not is_admin():
        return jsonify({"success": False, "message": _("You don't have permission to upload videos")}), 403
    
    if "video" not in request.files or request.files["video"].filename == "":
        return jsonify({"success": False, "message": _("No video file selected")}), 400
    
    video_file = request.files["video"]
    file_ext = os.path.splitext(video_file.filename)[1].lower()
    
    # Validate file extension
    if file_ext not in {".mp4", ".mkv", ".avi", ".webm", ".mov"}:
        return jsonify({"success": False, "message": _("File type not supported. Allowed: MP4, MKV, AVI, WebM, MOV")}), 400
    
    # Save to temporary location with UUID filename
    tmp_file_path = os.path.join(tempfile.gettempdir(), f"pikaraoke_upload_{uuid.uuid4().hex}{file_ext}")
    
    try:
        video_file.save(tmp_file_path)
        
        # Queue for transcoding
        display_title = os.path.splitext(secure_filename(video_file.filename))[0]
        k.download_manager.queue_transcode_file(
            tmp_file_path,
            enqueue=False,
            user="Pikaraoke",
            title=display_title,
        )
        
        return jsonify({"success": True, "message": display_title})
    
    except Exception as e:
        logging.error(f"Error uploading video: {e}")
        try:
            if os.path.exists(tmp_file_path):
                os.remove(tmp_file_path)
        except:
            pass
        return jsonify({"success": False, "message": _("Error uploading video")}), 500


@files_bp.route("/files/tags/remove", methods=["POST"])
def remove_tag_route():
    """Remove a genre/mood/language tag value from every song that has it."""
    if not is_admin():
        # MSG: Message shown after trying to use an admin-only feature without permissions.
        return jsonify({"success": False, "message": _("You don't have permission to do this")})

    k = get_karaoke_instance()
    category = request.form.get("category", "")
    value = request.form.get("value", "").strip()

    if category not in tag_manager.CATEGORY_KEYS or not value:
        # MSG: Message shown when an invalid tag category/value is given.
        return jsonify({"success": False, "message": _("Invalid tag")})

    updated = tag_manager.remove_tag_value(k.available_songs, category, value)
    return jsonify({"success": True, "updated": updated})


@files_bp.route("/files/tags/rename", methods=["POST"])
def rename_tag_route():
    """Rename (or merge) a genre/mood/language tag value across every song that has it."""
    if not is_admin():
        # MSG: Message shown after trying to use an admin-only feature without permissions.
        return jsonify({"success": False, "message": _("You don't have permission to do this")})

    k = get_karaoke_instance()
    category = request.form.get("category", "")
    old_value = request.form.get("old_value", "").strip()
    new_value = request.form.get("new_value", "").strip()

    if category not in tag_manager.CATEGORY_KEYS or not old_value or not new_value:
        # MSG: Message shown when an invalid tag category/value is given.
        return jsonify({"success": False, "message": _("Invalid tag")})

    updated = tag_manager.rename_tag_value(k.available_songs, category, old_value, new_value)
    return jsonify({"success": True, "updated": updated})


@files_bp.route("/files/tags/rescan", methods=["POST"])
def rescan_tag_route():
    """Ask the LLM to check every song that doesn't yet have this tag for a match."""
    if not is_admin():
        # MSG: Message shown after trying to use an admin-only feature without permissions.
        return jsonify({"success": False, "message": _("You don't have permission to do this")})

    k = get_karaoke_instance()
    category = request.form.get("category", "")
    value = request.form.get("value", "").strip()

    if category not in tag_manager.CATEGORY_KEYS or not value:
        # MSG: Message shown when an invalid tag category/value is given.
        return jsonify({"success": False, "message": _("Invalid tag")})

    with_tag = []
    without_tag = {}
    for path in k.available_songs:
        tags = tag_manager.read_tags(path)
        if value in (tags.get(category) or []):
            with_tag.append(path)
        else:
            without_tag[path] = k.filename_from_path(path)

    if not with_tag:
        # MSG: Message shown when a tag to rescan has no songs carrying it to use as a reference example.
        return jsonify({"success": False, "message": _("No songs currently have this tag")})

    origin_title = k.filename_from_path(with_tag[0])
    model = getattr(k, "llm_rename_model", None)
    matched_paths = tag_suggester.backfill_tag_across_library(
        category, value, origin_title, without_tag, model=model
    )
    updated = tag_suggester.apply_tag_to_songs(category, value, matched_paths)
    return jsonify({"success": True, "updated": updated})
