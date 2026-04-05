"""Admin log viewer routes."""

from flask import Blueprint, jsonify, render_template, request

from pikaraoke.lib.current_app import is_admin

logs_bp = Blueprint("logs", __name__)

# Will be set by app.py after the handler is created
_log_handler = None


def set_log_handler(handler):
    """Set the log buffer handler reference for this module."""
    global _log_handler
    _log_handler = handler


@logs_bp.route("/logs")
def logs():
    """Admin log viewer page.
    ---
    tags:
      - Admin
    responses:
      200:
        description: HTML log viewer page
    """
    if not is_admin():
        return render_template("login.html")
    return render_template("logs.html")


@logs_bp.route("/logs/entries")
def log_entries():
    """Return buffered log entries as JSON.
    ---
    tags:
      - Admin
    parameters:
      - name: level
        in: query
        type: string
        required: false
        description: Minimum log level (DEBUG, INFO, WARNING, ERROR)
      - name: limit
        in: query
        type: integer
        required: false
        description: Maximum number of entries to return
    responses:
      200:
        description: JSON array of log entries
    """
    if not is_admin():
        return jsonify([])
    if _log_handler is None:
        return jsonify([])
    level = request.args.get("level")
    limit = request.args.get("limit", type=int)
    return jsonify(_log_handler.get_entries(level=level, limit=limit))
