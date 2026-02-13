"""User preferences management routes."""

import flask_babel
from flask import Blueprint, flash, jsonify, redirect, request, url_for

from pikaraoke.lib.current_app import get_karaoke_instance, is_admin

preferences_bp = Blueprint("preferences", __name__)

_ = flask_babel.gettext


@preferences_bp.route("/change_preferences", methods=["GET"])
def change_preferences():
    """Change a user preference setting.
    ---
    tags:
      - Preferences
    parameters:
      - name: pref
        in: query
        type: string
        required: true
        description: Preference key to change
      - name: val
        in: query
        type: string
        required: true
        description: New value for the preference
    responses:
      200:
        description: JSON result of preference change
      302:
        description: Redirects to info page if not admin
    """
    k = get_karaoke_instance()
    if is_admin():
        preference = request.args["pref"]
        val = request.args["val"]

        # Server-side validation for known numeric preference ranges
        if preference.startswith("mic_fx_rack_"):
            try:
                n = int(val)
            except Exception:
                return jsonify([False, _("FX rack must be an integer between 0 and 4")])
            if n < 0 or n > 4:
                return jsonify([False, _("FX rack must be between 0 and 4")])

        if preference.startswith("mic_channel_"):
            try:
                n = int(val)
            except Exception:
                return jsonify([False, _("Channel must be an integer between 0 and 17")])
            if n < 0 or n > 17:
                return jsonify([False, _("Channel must be between 0 and 17")])

        if preference == "microphone_count":
            try:
                n = int(val)
            except Exception:
                return jsonify([False, _("Number of microphones must be an integer between 1 and 16")])
            if n < 1 or n > 16:
                return jsonify([False, _("Number of microphones must be between 1 and 16")])

        if preference == "queue_closing_time":
            val = val.strip()

        success, message = k.preferences.set(preference, val)
        return jsonify([success, message])
    else:
        # MSG: Message shown after trying to change preferences without admin permissions.
        flash(_("You don't have permission to change preferences"), "is-danger")
    return redirect(url_for("info.info"))


@preferences_bp.route("/clear_preferences", methods=["GET"])
def clear_preferences():
    """Reset all preferences to defaults.
    ---
    tags:
      - Preferences
    responses:
      302:
        description: Redirects to home page
    """
    k = get_karaoke_instance()
    if is_admin():
        success, message = k.preferences.reset_all()
        if success:
            k.update_now_playing_socket()
        flash(message, "is-success" if success else "is-danger")
    else:
        # MSG: Message shown after trying to clear preferences without admin permissions.
        flash(_("You don't have permission to clear preferences"), "is-danger")
    return redirect(url_for("home.home"))
