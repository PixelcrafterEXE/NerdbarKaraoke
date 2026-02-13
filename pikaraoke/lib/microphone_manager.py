"""Microphone tracking and assignment management."""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable


DEFAULT_COLOR_HEX = ["#cc0000", "#0047ab", "#009900", "#b8860b"]


class MicrophoneManager:
    """Manages microphone assignments using index-based IDs.

    External API is preserved (assign/release/get_user_microphone) but the
    identifier for a microphone is now its 1-based index as a string (e.g. "1").
    """

    def __init__(self, socketio=None, microphone_count: int = 4, microphone_colors: Iterable[str] | None = None) -> None:
        """Initialize MicrophoneManager.

        Args:
            socketio: SocketIO instance for real-time updates.
            microphone_count: Number of microphones to track.
            microphone_colors: Iterable of color hex strings (may be shorter than count).
        """
        self.socketio = socketio
        self.assignment_timestamps: dict[str, float] = {}

        # Normalize inputs
        try:
            self.microphone_count = max(1, int(microphone_count))
        except Exception:
            self.microphone_count = 4

        if microphone_colors:
            provided_colors = [c.strip() for c in microphone_colors if c.strip()]
            colors_list = provided_colors + [None] * max(0, self.microphone_count - len(provided_colors))
        else:
            colors_list = [DEFAULT_COLOR_HEX[i % len(DEFAULT_COLOR_HEX)] for i in range(self.microphone_count)]

        # Map microphone id (int) -> hex color or None when unset
        self.color_map: dict[int, str | None] = {
            i + 1: colors_list[i] for i in range(self.microphone_count)
        }

        # Internal assignment map: mic_id (1, 2, ...) -> username | None
        self.microphones: dict[int, str | None] = {i + 1: None for i in range(self.microphone_count)}

        logging.debug("MicrophoneManager initialized: count=%d colors=%s", self.microphone_count, self.color_map)

    def _valid_id(self, mic_id: int | str) -> bool:
        """Accept int mic_id or numeric string; normalize to int for membership checks."""
        try:
            mid = int(mic_id)
        except Exception:
            return False
        return mid in self.microphones

    def assign_microphone(self, mic_id: int | str, username: str) -> tuple[bool, str]:
        """Assign by numeric mic id (accepts int or numeric-string)."""
        if not self._valid_id(mic_id):
            return (False, f"Invalid microphone id: {mic_id}")
        mid = int(mic_id)

        if not username or not username.strip():
            return (False, "Username cannot be empty")
        username = username.strip()

        # If user already has a mic, release it first
        current_mic = self.get_user_microphone(username)
        if current_mic and current_mic != mid:
            self.release_microphone_by_user(username)

        current_user = self.microphones.get(mid)
        if current_user and current_user != username:
            logging.info("Microphone %s reassigned from %s to %s", mid, current_user, username)

        self.microphones[mid] = username
        self.assignment_timestamps[username] = time.time()
        logging.info("Assigned microphone %s to %s", mid, username)

        if self.socketio:
            self.socketio.emit("microphone_update", self.to_dict())

        return (True, f"Microphone #{mid} assigned to {username}")

    def release_microphone(self, mic_id: int | str) -> tuple[bool, str]:
        if not self._valid_id(mic_id):
            return (False, f"Invalid microphone id: {mic_id}")
        mid = int(mic_id)
        username = self.microphones.get(mid)
        if username:
            self.microphones[mid] = None
            if username in self.assignment_timestamps:
                del self.assignment_timestamps[username]
            logging.info("Released microphone %s from %s", mid, username)
            if self.socketio:
                self.socketio.emit("microphone_update", self.to_dict())
            return (True, f"Microphone #{mid} released")
        return (False, f"Microphone #{mid} was not assigned")
    def release_microphone_by_user(self, username: str) -> tuple[bool, str]:
        for mic_id, assigned_user in self.microphones.items():
            if assigned_user == username:
                return self.release_microphone(mic_id)
        return (False, f"User {username} does not have a microphone assigned")

    def get_user_microphone(self, username: str) -> int | None:
        for mic_id, assigned_user in self.microphones.items():
            if assigned_user == username:
                return mic_id
        return None

    def has_microphone(self, username: str) -> bool:
        return self.get_user_microphone(username) is not None

    def get_microphone_user(self, mic_id: int | str) -> str | None:
        if not self._valid_id(mic_id):
            return None
        mid = int(mic_id)
        return self.microphones.get(mid)

    def get_all_assignments(self) -> dict[int, str | None]:
        return self.microphones.copy()

    def get_users_with_microphones(self) -> set[str]:
        return {username for username in self.microphones.values() if username is not None}
    def reset_all_microphones(self) -> None:
        for mic_id in list(self.microphones.keys()):
            self.microphones[mic_id] = None
        self.assignment_timestamps.clear()
        logging.info("All microphones reset")
        if self.socketio:
            self.socketio.emit("microphone_update", self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        # jsonify will convert int keys to strings for JSON transport — keep ints internally
        return {"microphones": self.microphones.copy(), "colors": self.color_map.copy(), "count": self.microphone_count}

    def get_ids(self) -> list[int]:
        return list(self.microphones.keys())

