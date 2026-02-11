"""Microphone assignment routes."""

import flask_babel
import logging
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from pikaraoke.lib.current_app import get_karaoke_instance, is_admin

microphone_bp = Blueprint("microphone", __name__)

_ = flask_babel.gettext


@microphone_bp.route("/nfc/<color>")
def nfc_scan(color):
    """Handle NFC tag scan for microphone assignment.
    
    This route is opened when scanning an NFC tag on a microphone.
    It assigns the user's username (from cookies) to the microphone.
    
    Args:
        color: The microphone color (Red, Blue, Green, Yellow).
    ---
    tags:
      - Microphone
    parameters:
      - name: color
        in: path
        type: string
        required: true
        description: Microphone color (Red, Blue, Green, Yellow)
    responses:
      200:
        description: HTML page for microphone assignment
      302:
        description: Redirects to home page after assignment
    """
    k = get_karaoke_instance()
    
    # Validate microphone color
    if color not in k.microphone_manager.MICROPHONE_COLORS:
        flash(f"Invalid microphone color: {color}", "is-danger")
        return redirect(url_for("home.home"))
    
    # Get username from cookies
    username = request.cookies.get("user", "").strip()
    
    # If no username, show prompt page
    if not username:
        return render_template(
            "microphone_assign.html",
            site_title="Microphone Assignment",
            title=f"Assign {color} Microphone",
            microphone_color=color,
        )
    
    # Assign the microphone
    success, message = k.microphone_manager.assign_microphone(color, username)
    
    if success:
        flash(message, "is-success")
    else:
        flash(message, "is-danger")
    
    return redirect(url_for("home.home"))


@microphone_bp.route("/assign_microphone", methods=["POST"])
def assign_microphone():
    """Assign a microphone to a user (with username input).
    ---
    tags:
      - Microphone
    parameters:
      - name: color
        in: formData
        type: string
        required: true
        description: Microphone color
      - name: username
        in: formData
        type: string
        required: true
        description: Username to assign
    responses:
      302:
        description: Redirects to home page
    """
    k = get_karaoke_instance()
    
    color = request.form.get("color", "").strip()
    username = request.form.get("username", "").strip()
    
    if not username:
        flash("Please enter a username", "is-danger")
        return redirect(url_for("microphone.nfc_scan", color=color))
    
    success, message = k.microphone_manager.assign_microphone(color, username)
    
    if success:
        flash(message, "is-success")
    else:
        flash(message, "is-danger")
    
    # Create response with username cookie
    response = redirect(url_for("home.home"))
    response.set_cookie("user", username, max_age=60 * 60 * 24 * 365)  # 1 year
    return response


@microphone_bp.route("/release_microphone", methods=["POST", "GET"])
def release_microphone():
    """Release a user's microphone assignment.
    ---
    tags:
      - Microphone
    responses:
      200:
        description: JSON response with success status
      302:
        description: Redirects to home page
    """
    k = get_karaoke_instance()
    
    # Get username from cookies
    username = request.cookies.get("user", "").strip()
    
    if not username:
        flash("No username found", "is-danger")
        return redirect(url_for("home.home"))
    
# Determine which mic is being released so we can disable its effect
    color = k.microphone_manager.get_user_microphone(username)
    success, message = k.microphone_manager.release_microphone_by_user(username)

    # Disable OSC for the released mic (in memory only)
    if success and color:
        try:
            k.effects_manager.disable_microphone_input(color)
        except Exception:
            # best-effort; don't break release flow
            logging.exception("Failed to disable effects for released microphone %s", color)
    
    if request.method == "POST" or request.args.get("ajax"):
        return jsonify({"success": success, "message": message})
    
    if success:
        flash(message, "is-success")
    else:
        flash(message, "is-danger")
    
    return redirect(url_for("home.home"))


@microphone_bp.route("/microphone_status")
def microphone_status():
    """Get current microphone assignments.
    ---
    tags:
      - Microphone
    responses:
      200:
        description: JSON with microphone assignments
    """
    k = get_karaoke_instance()
    return jsonify(k.microphone_manager.to_dict())


@microphone_bp.route("/admin/unassign_microphone/<color>", methods=["POST"])
def admin_unassign_microphone(color):
    """Unassign a specific microphone (admin only).
    ---
    tags:
      - Microphone
      - Admin
    parameters:
      - name: color
        in: path
        type: string
        required: true
        description: Microphone color to unassign
    responses:
      200:
        description: JSON response with success status
      403:
        description: Unauthorized
    """
    k = get_karaoke_instance()
    
    if not is_admin():
        return jsonify({"success": False, "message": "You don't have permission to unassign microphones"}), 403
    
    success, message = k.microphone_manager.release_microphone(color)
    if success:
        try:
            k.effects_manager.disable_microphone_input(color)
        except Exception:
            logging.exception("Failed to disable effects for unassigned microphone %s", color)
    return jsonify({"success": success, "message": message})


@microphone_bp.route("/admin/reset_microphones", methods=["POST", "GET"])
def reset_microphones():
    """Reset all microphone assignments (admin only).
    ---
    tags:
      - Microphone
      - Admin
    responses:
      302:
        description: Redirects to home page
    """
    k = get_karaoke_instance()
    
    if not is_admin():
        flash("You don't have permission to reset microphones", "is-danger")
        return redirect(url_for("home.home"))
    
    k.microphone_manager.reset_all_microphones()
    # Disable effects for all microphones (best-effort, in memory only)
    for color in k.microphone_manager.MICROPHONE_COLORS:
        try:
            k.effects_manager.disable_microphone_input(color)
        except Exception:
            logging.exception("Failed to disable effects for microphone %s during reset", color)
    flash("All microphones have been reset", "is-success")
    
    return redirect(url_for("home.home"))
