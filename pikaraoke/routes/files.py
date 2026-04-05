"""File management routes for browsing, editing, and deleting songs."""

import json
import logging
import os
import tempfile
import uuid
from urllib.parse import unquote

import flask_babel
from flask import Blueprint, flash, redirect, render_template, request, url_for, jsonify
from flask_paginate import Pagination, get_page_parameter
from werkzeug.utils import secure_filename

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
    return render_template(
        "files.html",
        pagination=pagination,
        sort_order=sort_order,
        site_title=site_name,
        letter=letter,
        # MSG: Title of the files page.
        title=_("Browse"),
        songs=songs[start_index : start_index + results_per_page],
        admin=is_admin(),
        match_users=match_users,
        all_liked_users=sorted(k.likes_manager.likes.keys()),
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
            )
    else:
        d = request.form.to_dict()
        if "new_file_name" in d and "old_file_name" in d:
            new_name = d["new_file_name"]
            old_name = d["old_file_name"]
            if k.queue_manager.is_song_in_queue(old_name):
                # check one more time just in case someone added it during editing
                flash(queue_error_msg + old_name, "is-danger")
            else:
                # check if new_name already exist
                file_extension = os.path.splitext(old_name)[1]
                if os.path.isfile(os.path.join(k.download_path, new_name + file_extension)):
                    flash(
                        # MSG: Message shown after trying to rename a file to a name that already exists.
                        _("Error renaming file: '%s' to '%s', Filename already exists")
                        % (old_name, new_name + file_extension),
                        "is-danger",
                    )
                else:
                    k.rename(old_name, new_name)
                    flash(
                        # MSG: Message shown after renaming a file.
                        _("Renamed file: %s to %s") % (old_name, new_name),
                        "is-warning",
                    )
        else:
            # MSG: Message shown after trying to edit a song without specifying the filename.
            flash(_("Error: No filename parameters were specified!"), "is-danger")
        return redirect(url_for("files.browse"))


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
