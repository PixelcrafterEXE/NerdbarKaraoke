"""Routes for song like/unlike and liked-songs queries."""

from __future__ import annotations

import json

import flask_babel
from flask import Blueprint, jsonify, request

from pikaraoke.lib.current_app import get_karaoke_instance

_ = flask_babel.gettext

likes_bp = Blueprint("likes", __name__)


@likes_bp.route("/likes/toggle", methods=["POST"])
def toggle_like():
    """Toggle the like status of a song for a user.
    ---
    tags:
      - Likes
    consumes:
      - application/x-www-form-urlencoded
    parameters:
      - name: song
        in: formData
        type: string
        required: true
        description: Song file path
      - name: user
        in: formData
        type: string
        required: true
        description: Username
    responses:
      200:
        description: New like state
    """
    k = get_karaoke_instance()
    d = request.form.to_dict() if request.form else request.args.to_dict()
    song = d.get("song", "")
    user = d.get("user", "").strip()
    if not song or not user:
        return jsonify({"success": False, "error": "Missing song or user"}), 400

    if k.likes_manager.is_liked(user, song):
        k.likes_manager.unlike(user, song)
        liked = False
    else:
        k.likes_manager.like(user, song)
        liked = True

    return jsonify({
        "success": True,
        "liked": liked,
        "like_count": k.likes_manager.get_like_count(song),
    })


@likes_bp.route("/likes/status")
def like_status():
    """Check if the current user has liked a song.
    ---
    tags:
      - Likes
    parameters:
      - name: song
        in: query
        type: string
        required: true
      - name: user
        in: query
        type: string
        required: true
    responses:
      200:
        description: Like status
    """
    k = get_karaoke_instance()
    song = request.args.get("song", "")
    user = request.args.get("user", "").strip()
    return jsonify({
        "liked": k.likes_manager.is_liked(user, song) if user else False,
        "like_count": k.likes_manager.get_like_count(song),
    })


@likes_bp.route("/likes/songs")
def liked_songs():
    """Return the list of song paths liked by a user.
    ---
    tags:
      - Likes
    parameters:
      - name: user
        in: query
        type: string
        required: true
    responses:
      200:
        description: List of liked song paths
    """
    k = get_karaoke_instance()
    user = request.args.get("user", "").strip()
    songs = sorted(k.likes_manager.get_liked_songs(user))
    return jsonify(songs)


@likes_bp.route("/likes/counts")
def like_counts():
    """Return like counts for all songs.
    ---
    tags:
      - Likes
    responses:
      200:
        description: Mapping of song path to like count
    """
    k = get_karaoke_instance()
    return jsonify(k.likes_manager.get_all_like_counts())
