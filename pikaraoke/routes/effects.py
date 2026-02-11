"""Effects control routes."""

from __future__ import annotations

import flask_babel
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin

_ = flask_babel.gettext

effects_bp = Blueprint("effects", __name__)


def _get_user_microphone(karaoke_instance) -> str | None:
    username = request.cookies.get("user", "").strip()
    if not username:
        return None
    return karaoke_instance.microphone_manager.get_user_microphone(username)


@effects_bp.route("/effects")
def effects():
    """Effects control page.
    ---
    tags:
      - Pages
    responses:
      200:
        description: HTML page with effects controls
    """
    k = get_karaoke_instance()
    if not k.effects_enabled:
        flash(_("Effects are disabled"), "is-warning")
        return redirect(url_for("home.home"))
    site_name = get_site_name()
    user_microphone = _get_user_microphone(k)

    if not is_admin() and not user_microphone:
        flash(_("You don't have permission to access effects"), "is-danger")
        return redirect(url_for("home.home"))

    return render_template(
        "effects.html",
        site_title=site_name,
        title=_("Effects"),
        admin=is_admin(),
        user_microphone=user_microphone,
    )


@effects_bp.route("/effects/config")
def effects_config():
    """Return effect config list."""
    k = get_karaoke_instance()
    if not k.effects_enabled:
        return jsonify({"effects": []})
    if is_admin():
        return jsonify({"effects": k.effects_manager.get_effects_for_admin()})
    return jsonify({"effects": k.effects_manager.get_effects_config()})


@effects_bp.route("/effects/state")
def effects_state():
    """Return current effect state for admin or mic holder."""
    k = get_karaoke_instance()
    if not k.effects_enabled:
        return jsonify({"effects": []})
    
    user_microphone = _get_user_microphone(k)
    
    if is_admin():
        # Admin gets both admin view and user view if they have a microphone
        response = {"effects": k.effects_manager.get_effects_for_admin()}
        if user_microphone:
            # Merge in user state for admin's own microphone
            user_state = k.effects_manager.get_user_state(user_microphone)
            # Add user state without overwriting the admin effects list
            response["microphone"] = user_state.get("microphone")
            response["effect_id"] = user_state.get("effect_id")
            response["parameters"] = user_state.get("parameters")
            response["user_effects"] = user_state.get("effects")  # User's filtered list
        return jsonify(response)

    if not user_microphone:
        return jsonify({"error": _("No microphone assigned")}), 403

    return jsonify(k.effects_manager.get_user_state(user_microphone))


@effects_bp.route("/effects/admin/update", methods=["POST"])
def effects_admin_update():
    """Update effect settings for a microphone (admin only)."""
    k = get_karaoke_instance()
    if not k.effects_enabled:
        return jsonify({"success": False, "message": _("Effects disabled")}), 403
    if not is_admin():
        return jsonify({"success": False, "message": _("Unauthorized")}), 403

    data = request.get_json(silent=True) or {}
    effect_id = data.get("effect_id")
    if not effect_id:
        return jsonify({"success": False, "message": "Missing effect"}), 400

    if "parameters" in data and isinstance(data.get("parameters"), dict):
        k.effects_manager.update_effect_defaults(effect_id, data.get("parameters"))

    if "user_editable" in data and isinstance(data.get("user_editable"), dict):
        k.effects_manager.update_user_editable(effect_id, data.get("user_editable"))

    if "visible" in data:
        k.effects_manager.update_effect_visibility(effect_id, bool(data.get("visible")))

    return jsonify({"success": True})


@effects_bp.route("/effects/user/update", methods=["POST"])
def effects_user_update():
    """Update effect parameter values for the current user's microphone."""
    k = get_karaoke_instance()
    if not k.effects_enabled:
        return jsonify({"success": False, "message": _("Effects disabled")}), 403
    user_microphone = _get_user_microphone(k)
    if not user_microphone:
        return jsonify({"success": False, "message": _("No microphone assigned")}), 403

    data = request.get_json(silent=True) or {}
    effect_id = data.get("effect_id")
    parameters = data.get("parameters", {})
    if parameters and not isinstance(parameters, dict):
        return jsonify({"success": False, "message": "Invalid parameters"}), 400
    if effect_id:
        if effect_id == "none":
            k.effects_manager.disable_microphone_input(user_microphone)
        else:
            success, message = k.effects_manager.set_microphone_effect(user_microphone, effect_id)
            if not success:
                return jsonify({"success": False, "message": _(message)}), 400
            if isinstance(parameters, dict) and parameters:
                k.effects_manager.update_user_parameters(user_microphone, parameters)
            k.effects_manager.apply_effect_to_mixer(user_microphone)

    elif isinstance(parameters, dict) and parameters:
        k.effects_manager.update_user_parameters(user_microphone, parameters)
        k.effects_manager.apply_effect_to_mixer(user_microphone)

    return jsonify({"success": True})
