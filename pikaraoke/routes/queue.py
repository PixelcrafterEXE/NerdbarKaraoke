"""Song queue management routes."""

import html
import json
import logging

import flask_babel
from flask import Blueprint, flash, redirect, render_template, request, url_for

from pikaraoke.lib.current_app import (
    broadcast_event,
    get_karaoke_instance,
    get_site_name,
    is_admin,
)

try:
    from urllib.parse import unquote
except ImportError:
    from urllib import unquote

_ = flask_babel.gettext

queue_bp = Blueprint("queue", __name__)


@queue_bp.route("/queue")
def queue():
    """Queue management page.
    ---
    tags:
      - Pages
    responses:
      200:
        description: HTML queue management page
    """
    k = get_karaoke_instance()
    site_name = get_site_name()
    return render_template(
        "queue.html",
        queue=k.queue_manager.queue,
        site_title=site_name,
        title="Queue",
        admin=is_admin(),
      queue_mode=k.queue_mode,
    )


@queue_bp.route("/get_queue")
def get_queue():
    """Get the current song queue.
    ---
    tags:
      - Queue
    responses:
      200:
        description: List of songs in queue
        schema:
          type: array
          items:
            type: object
            properties:
              user:
                type: string
                description: User who added the song
              file:
                type: string
                description: File path of the song
              title:
                type: string
                description: Display title of the song
              semitones:
                type: integer
                description: Transpose value in semitones
    """
    k = get_karaoke_instance()
    user = request.cookies.get("user")
    user = user.strip() if isinstance(user, str) else None
    queue_with_ratings = []
    shadowban_base = k.queue_manager.get_shadowban_base_rating()
    for item in k.queue_manager.queue:
        entry = dict(item)
        entry["rating"] = k.queue_manager.get_display_rating(
            item["file"], user, shadowban_base
        )
        entry["shadowbanned"] = k.queue_manager.is_shadowbanned(item["file"])
        entry["user_vote"] = (
            k.queue_manager.get_user_vote(item["file"], user) if user else 0
        )
        entry["duration"] = k.queue_manager.get_song_duration(item["file"])
        queue_with_ratings.append(entry)
    return json.dumps(queue_with_ratings)


@queue_bp.route("/queue/addrandom", methods=["GET"])
def add_random():
    """Add random songs to the queue.
    ---
    tags:
      - Queue
    parameters:
      - name: amount
        in: query
        type: integer
        required: true
        description: Number of random songs to add
    responses:
      302:
        description: Redirects to queue page
    """
    k = get_karaoke_instance()
    amount = int(request.args["amount"])
    rc = k.queue_manager.queue_add_random(amount)
    if rc:
        # MSG: Message shown after adding random tracks
        flash(_("Added %s random tracks") % amount, "is-success")
    else:
        # MSG: Message shown after running out songs to add during random track addition
        flash(_("Ran out of songs!"), "is-warning")
    broadcast_event("queue_update")
    return redirect(url_for("queue.queue"))


@queue_bp.route("/queue/reorder", methods=["POST"])
def reorder():
    """Handle drag-and-drop reordering of the queue.
    ---
    tags:
      - Queue
    consumes:
      - application/x-www-form-urlencoded
    parameters:
      - name: old_index
        in: formData
        type: integer
        required: true
        description: The current index of the item to move
      - name: new_index
        in: formData
        type: integer
        required: true
        description: The target index to move the item to
    responses:
      200:
        description: Result of the reorder operation
        schema:
          type: object
          properties:
            success:
              type: boolean
              description: Whether the reorder was successful
            error:
              type: string
              description: Error message if failed
      403:
        description: Unauthorized access (admin only)
    """
    if not is_admin():
        return json.dumps({"success": False, "error": _('Unauthorized')}), 403

    k = get_karaoke_instance()
    try:
        old_index = int(request.form["old_index"])
        new_index = int(request.form["new_index"])

        # Security bounds check
        if 0 <= old_index < len(k.queue_manager.queue) and 0 <= new_index < len(
            k.queue_manager.queue
        ):
            item = k.queue_manager.queue.pop(old_index)
            k.queue_manager.queue.insert(new_index, item)
            broadcast_event("queue_update")
            k.update_now_playing_socket()
            return json.dumps({"success": True})
    except (ValueError, IndexError):
        pass

    return json.dumps({"success": False})


@queue_bp.route("/queue/edit", methods=["GET"])
def queue_edit():
    """Edit queue items (admin only)."""
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not is_admin():
        if is_ajax:
            return json.dumps({"success": False, "error": _('Unauthorized')}), 403
        # MSG: Message shown when non-admin tries to edit queue
        flash(_("Unauthorized"), "is-danger")
        return redirect(url_for("queue.queue"))

    k = get_karaoke_instance()
    action = request.args["action"]
    success = False
    message = ""

    if action == "clear":
        k.queue_manager.queue_clear()
        message = _("Cleared the queue!")
        if not is_ajax:
            # MSG: Message shown after clearing the queue
            flash(message, "is-warning")
        broadcast_event("skip", "clear queue")
        success = True
    else:
        song = request.args["song"]
        song = unquote(song)

        # Handle "Move to Top" (Play Next) locally to avoid modifying karaoke.py
        if action == "top":
            found_index = -1
            for i, item in enumerate(k.queue_manager.queue):
                if song in item["file"]:
                    found_index = i
                    break

            # Move to index 0 (top of queue = next to play after current song)
            if found_index > 0:
                item = k.queue_manager.queue.pop(found_index)
                k.queue_manager.queue.insert(0, item)
                message = _("Moved to top of queue") + ": " + k.filename_from_path(item["file"])
                if not is_ajax:
                    flash(message, "is-success")
                success = True

        # Handle "Move to Bottom" locally
        elif action == "bottom":
            found_index = -1
            for i, item in enumerate(k.queue_manager.queue):
                if song in item["file"]:
                    found_index = i
                    break

            if found_index >= 0 and found_index < len(k.queue_manager.queue) - 1:
                item = k.queue_manager.queue.pop(found_index)
                k.queue_manager.queue.append(item)
                message = _("Moved to bottom of queue") + ": " + k.filename_from_path(item["file"])
                if not is_ajax:
                    flash(message, "is-success")
                success = True

        elif action == "down":
            result = k.queue_manager.queue_edit(song, "down")
            if result:
                message = _("Moved down in queue") + ": " + k.filename_from_path(song)
                if not is_ajax:
                    # MSG: Message shown after moving a song down in the queue
                    flash(message, "is-success")
                success = True
            else:
                message = _("Error moving down in queue") + ": " + k.filename_from_path(song)
                if not is_ajax:
                    # MSG: Message shown after failing to move a song down in the queue
                    flash(message, "is-danger")
        elif action == "up":
            result = k.queue_manager.queue_edit(song, "up")
            if result:
                message = _("Moved up in queue") + ": " + k.filename_from_path(song)
                if not is_ajax:
                    # MSG: Message shown after moving a song up in the queue
                    flash(message, "is-success")
                success = True
            else:
                message = _("Error moving up in queue") + ": " + k.filename_from_path(song)
                if not is_ajax:
                    # MSG: Message shown after failing to move a song up in the queue
                    flash(message, "is-danger")
        elif action == "delete":
            result = k.queue_manager.queue_edit(song, "delete")
            if result:
                message = _("Deleted from queue") + ": " + k.filename_from_path(song)
                if not is_ajax:
                    # MSG: Message shown after deleting a song from the queue
                    flash(message, "is-success")
                success = True
            else:
                message = _("Error deleting from queue") + ": " + k.filename_from_path(song)
                if not is_ajax:
                    # MSG: Message shown after failing to delete a song from the queue
                    flash(message, "is-danger")
        elif action == "shadowban":
          matched_file = None
          for item in k.queue_manager.queue:
            if item["file"] == song:
              matched_file = item["file"]
              break
          if matched_file is None:
            for item in k.queue_manager.queue:
              if song in item["file"] or item["file"] in song:
                matched_file = item["file"]
                break

          if matched_file:
            now_shadowbanned = k.queue_manager.toggle_shadowban(matched_file)
            if k.queue_mode == "fair":
              k.queue_manager._reorder_queue_fair()
            elif k.queue_mode == "democratic":
              k.queue_manager._reorder_queue_by_votes()
            message = (
              _("Shadowbanned") if now_shadowbanned else _("Unshadowbanned")
            ) + ": " + html.escape(k.filename_from_path(matched_file))
            if not is_ajax:
              flash(message, "is-warning")
            success = True
          else:
            message = _("Song not found in queue") + ": " + html.escape(k.filename_from_path(song))
            if not is_ajax:
              flash(message, "is-danger")

    if success:
        broadcast_event("queue_update")
        # Ensure splash screen "up next" is updated
        k.update_now_playing_socket()

    if is_ajax:
        return json.dumps({"success": success, "message": message})
    return redirect(url_for("queue.queue"))


@queue_bp.route("/enqueue", methods=["POST", "GET"])
def enqueue():
    """Add a song to the queue.
    ---
    tags:
      - Queue
    parameters:
      - name: song
        in: query
        type: string
        description: Path to the song file
      - name: user
        in: query
        type: string
        description: Name of the user adding the song
      - name: song-to-add
        in: formData
        type: string
        description: Path to the song file (form data)
      - name: song-added-by
        in: formData
        type: string
        description: Name of the user (form data)
    responses:
      200:
        description: Result of enqueue operation
        schema:
          type: object
          properties:
            song:
              type: string
              description: Title of the song
            success:
              type: boolean
              description: Whether the song was added
    """
    k = get_karaoke_instance()
    if "song" in request.args:
        song = request.args["song"]
    else:
        d = request.form.to_dict()
        song = d["song-to-add"]
    if "user" in request.args:
        user = request.args["user"]
    else:
        d = request.form.to_dict()
        user = d["song-added-by"]
    # Support additional requestees (comma-separated or repeated param)
    additional_users: list[str] = []
    if "additional_users" in request.args:
        additional_users = [u.strip() for u in request.args["additional_users"].split(",") if u.strip()]
    elif request.form:
        d = request.form.to_dict()
        if "additional_users" in d:
            additional_users = [u.strip() for u in d["additional_users"].split(",") if u.strip()]
    rc = k.queue_manager.enqueue(song, user, additional_users=additional_users)
    broadcast_event("queue_update")
    song_title = html.escape(k.filename_from_path(song))

    response_data = {"song": song_title, "success": True, "message": _("Song added to the queue: %s") % song_title}
    
    if isinstance(rc, list):
        success = bool(rc[0])
        message = rc[1]
        closing_ts = rc[2] if len(rc) > 2 else None
        response_data = {"song": song_title, "success": success, "message": message}
        if closing_ts:
            response_data["closing_ts"] = closing_ts
        logging.debug(f"Enqueue response: success={success}, has_closing_ts={closing_ts is not None}, message={message[:50]}")
    elif rc is False:
        success = False
        message = _("Song is already in the queue: %s") % song_title
        response_data = {"song": song_title, "success": success, "message": message}
    else:
        success = True
        message = _("Song added to the queue: %s") % song_title
        response_data = {"song": song_title, "success": success, "message": message}

    return json.dumps(response_data)


@queue_bp.route("/queue/downloads")
def get_current_downloads():
    """Get the status of current and pending downloads.
    ---
    tags:
      - Queue
    responses:
      200:
        description: Status of active and pending downloads
        schema:
          type: object
          properties:
            active:
              type: object
              description: Currently active download info
            pending:
              type: array
              items:
                type: object
                description: Pending download info
            errors:
              type: array
              items:
                type: object
                description: Failed download info
    """
    k = get_karaoke_instance()
    return json.dumps(k.download_manager.get_downloads_status())


@queue_bp.route("/queue/downloads/errors/<error_id>", methods=["DELETE"])
def delete_download_error(error_id):
    """Remove a download error from the list.
    ---
    tags:
      - Queue
    parameters:
      - name: error_id
        in: path
        type: string
        required: true
        description: ID of the error to remove
    responses:
      200:
        description: Error removed successfully
      404:
        description: Error not found
    """
    k = get_karaoke_instance()
    if k.download_manager.remove_error(error_id):
        return json.dumps({"success": True})
    else:
        return json.dumps({"success": False, "error": "Error not found"}), 404


@queue_bp.route("/queue/vote", methods=["POST"])
def vote_on_song():
    """Vote on a song in the queue (upvote or downvote).
    ---
    tags:
      - Queue
    consumes:
      - application/json
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            song:
              type: string
              description: Path to the song file
            vote_type:
              type: string
              enum: [upvote, downvote]
              description: Type of vote
            user:
              type: string
              description: Username casting the vote
    responses:
      200:
        description: Vote recorded successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            net_votes:
              type: integer
              description: Total upvotes minus downvotes
            user_vote:
              type: integer
              description: 1, -1, or 0 for the current user's vote
      400:
        description: Missing required parameters or voting disabled
    """
    k = get_karaoke_instance()

    # Check if voting is enabled (democratic queue mode)
    if k.queue_mode != "democratic":
        return (
            json.dumps({"success": False, "error": "Voting is disabled"}),
            400,
        )

    try:
        data = request.get_json()
        song = data.get("song")
        vote_type = data.get("vote_type")
        user = data.get("user", "Anonymous")
        if isinstance(user, str):
          user = user.strip()
        # Fallback to cookie if user missing/anonymous
        if not user or user == "Anonymous":
          cookie_user = request.cookies.get("user")
          if cookie_user:
            user = cookie_user.strip()

        if not song or not vote_type:
            return (
                json.dumps({"success": False, "error": "Missing song or vote_type"}),
                400,
            )

        # Normalize song path to match queue items if possible
        matched_song = None
        for item in k.queue_manager.queue:
          if item["file"] == song:
            matched_song = item["file"]
            break
        if matched_song is None:
          for item in k.queue_manager.queue:
            if song in item["file"] or item["file"] in song:
              matched_song = item["file"]
              break
        if matched_song:
          song = matched_song

        result = k.queue_manager.vote_song(song, user, vote_type)
        broadcast_event("queue_update")
        return json.dumps(result)

    except Exception as e:
        logging.error(f"Error processing vote: {e}")
        return (
            json.dumps({"success": False, "error": "An internal error occurred processing your vote."}),
            500,
        )


