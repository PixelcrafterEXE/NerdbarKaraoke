"""Microphone tracking and assignment management."""

from __future__ import annotations

import logging
import time
from typing import Any


class MicrophoneManager:
    """Manages microphone assignments to users.

    This class handles tracking which users are assigned to which microphones,
    and provides methods for assigning, releasing, and checking microphone status.

    Attributes:
        microphones: Dictionary mapping microphone colors to assigned usernames.
        microphone_colors: List of available microphone colors.
    """

    # Available microphone colors
    MICROPHONE_COLORS = ["Red", "Blue", "Green", "Yellow"]

    def __init__(self, socketio=None) -> None:
        """Initialize the MicrophoneManager.

        Args:
            socketio: SocketIO instance for real-time event emission.
        """
        self.microphones: dict[str, str | None] = {color: None for color in self.MICROPHONE_COLORS}
        self.socketio = socketio
        self.assignment_timestamps: dict[str, float] = {}
        logging.debug("MicrophoneManager initialized")

    def assign_microphone(self, color: str, username: str) -> tuple[bool, str]:
        """Assign a microphone to a user.

        Args:
            color: The microphone color (Red, Blue, Green, Yellow).
            username: The username to assign the microphone to.

        Returns:
            Tuple of (success: bool, message: str).
        """
        if color not in self.MICROPHONE_COLORS:
            return (False, f"Invalid microphone color: {color}")

        if not username or not username.strip():
            return (False, "Username cannot be empty")

        username = username.strip()

        # Check if user already has a microphone
        current_mic = self.get_user_microphone(username)
        if current_mic and current_mic != color:
            # Release the old microphone first
            self.release_microphone_by_user(username)

        # Check if microphone is already assigned to someone else
        current_user = self.microphones[color]
        if current_user and current_user != username:
            logging.info(f"Microphone {color} reassigned from {current_user} to {username}")

        self.microphones[color] = username
        self.assignment_timestamps[username] = time.time()
        logging.info(f"Assigned {color} microphone to {username}")

        # Emit socket event for real-time updates
        if self.socketio:
            self.socketio.emit("microphone_update", self.get_all_assignments())

        return (True, f"{color} microphone assigned to {username}")

    def release_microphone(self, color: str) -> tuple[bool, str]:
        """Release a microphone assignment.

        Args:
            color: The microphone color to release.

        Returns:
            Tuple of (success: bool, message: str).
        """
        if color not in self.MICROPHONE_COLORS:
            return (False, f"Invalid microphone color: {color}")

        username = self.microphones[color]
        if username:
            self.microphones[color] = None
            if username in self.assignment_timestamps:
                del self.assignment_timestamps[username]
            logging.info(f"Released {color} microphone from {username}")

            # Emit socket event for real-time updates
            if self.socketio:
                self.socketio.emit("microphone_update", self.get_all_assignments())

            return (True, f"{color} microphone released")
        else:
            return (False, f"{color} microphone was not assigned")

    def release_microphone_by_user(self, username: str) -> tuple[bool, str]:
        """Release a microphone assignment by username.

        Args:
            username: The username to release the microphone from.

        Returns:
            Tuple of (success: bool, message: str).
        """
        for color, assigned_user in self.microphones.items():
            if assigned_user == username:
                return self.release_microphone(color)

        return (False, f"User {username} does not have a microphone assigned")

    def get_user_microphone(self, username: str) -> str | None:
        """Get the microphone color assigned to a user.

        Args:
            username: The username to check.

        Returns:
            The microphone color if assigned, None otherwise.
        """
        for color, assigned_user in self.microphones.items():
            if assigned_user == username:
                return color
        return None

    def has_microphone(self, username: str) -> bool:
        """Check if a user has a microphone assigned.

        Args:
            username: The username to check.

        Returns:
            True if the user has a microphone, False otherwise.
        """
        return self.get_user_microphone(username) is not None

    def get_microphone_user(self, color: str) -> str | None:
        """Get the username assigned to a microphone.

        Args:
            color: The microphone color to check.

        Returns:
            The username if assigned, None otherwise.
        """
        return self.microphones.get(color)

    def get_all_assignments(self) -> dict[str, str | None]:
        """Get all current microphone assignments.

        Returns:
            Dictionary mapping microphone colors to usernames.
        """
        return self.microphones.copy()

    def get_users_with_microphones(self) -> set[str]:
        """Get a set of all users who currently have microphones assigned.

        Returns:
            Set of usernames with microphones assigned.
        """
        return {username for username in self.microphones.values() if username is not None}

    def reset_all_microphones(self) -> None:
        """Release all microphone assignments."""
        for color in self.MICROPHONE_COLORS:
            self.microphones[color] = None
        self.assignment_timestamps.clear()
        logging.info("All microphones reset")

        # Emit socket event for real-time updates
        if self.socketio:
            self.socketio.emit("microphone_update", self.get_all_assignments())

    def to_dict(self) -> dict[str, Any]:
        """Convert microphone assignments to a dictionary for API responses.

        Returns:
            Dictionary with microphone assignments and metadata.
        """
        return {
            "microphones": self.microphones.copy(),
            "colors": self.MICROPHONE_COLORS,
        }
