"""Home page route."""

import flask_babel
from flask import Blueprint, render_template, request

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin

_ = flask_babel.gettext


home_bp = Blueprint("home", __name__)


@home_bp.route("/")
def home():
    """Home page with now playing info and controls.
    ---
    tags:
      - Pages
    responses:
      200:
        description: HTML home page
    """
    k = get_karaoke_instance()
    site_name = get_site_name()
    
    # Get current user from cookies
    username = request.cookies.get("user", "").strip()
    user_microphone = None
    if username:
        user_microphone = k.microphone_manager.get_user_microphone(username)
    
    # Get all microphone assignments
    microphone_assignments = k.microphone_manager.get_all_assignments()
    
    return render_template(
        "home.html",
        site_title=site_name,
        title="Home",
        transpose_value=k.now_playing_transpose,
        admin=is_admin(),
        is_transpose_enabled=k.is_transpose_enabled,
        volume=k.volume,
        username=username,
        user_microphone=user_microphone,
        microphone_assignments=microphone_assignments,
        show_microphone_status=k.show_microphone_status or is_admin(),
    )
