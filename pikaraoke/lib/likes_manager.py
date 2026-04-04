"""Server-side per-user song likes, persisted as JSON."""

from __future__ import annotations

import json
import logging
import os
from typing import Any


class LikesManager:
    """Manage per-user liked-song lists stored on disk.

    Data file layout (JSON)::

        {
            "alice": ["/songs/song1.mp4", "/songs/song2.mp4"],
            "bob":   ["/songs/song3.mp4"]
        }

    Attributes:
        likes: Mapping of username → set of liked song paths.
    """

    def __init__(self, data_directory: str | None = None) -> None:
        self.likes: dict[str, set[str]] = {}
        self._persist_path: str | None = None
        if data_directory:
            self._persist_path = os.path.join(data_directory, "likes.json")
            self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def like(self, user: str, song_path: str) -> None:
        """Add a song to a user's liked list."""
        user = user.strip()
        if not user:
            return
        self.likes.setdefault(user, set()).add(song_path)
        self._save()

    def unlike(self, user: str, song_path: str) -> None:
        """Remove a song from a user's liked list."""
        user = user.strip()
        if not user:
            return
        user_likes = self.likes.get(user)
        if user_likes and song_path in user_likes:
            user_likes.discard(song_path)
            if not user_likes:
                del self.likes[user]
            self._save()

    def is_liked(self, user: str, song_path: str) -> bool:
        """Check whether a user has liked a given song."""
        return song_path in self.likes.get(user.strip(), set())

    def get_liked_songs(self, user: str) -> set[str]:
        """Return the set of song paths liked by a user."""
        return set(self.likes.get(user.strip(), set()))

    def get_like_count(self, song_path: str) -> int:
        """Return how many distinct users have liked a song."""
        return sum(1 for liked in self.likes.values() if song_path in liked)

    def get_all_like_counts(self) -> dict[str, int]:
        """Return {song_path: like_count} for every liked song."""
        counts: dict[str, int] = {}
        for liked in self.likes.values():
            for song in liked:
                counts[song] = counts.get(song, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:
        if not self._persist_path:
            return
        try:
            serialisable = {user: sorted(songs) for user, songs in self.likes.items()}
            with open(self._persist_path, "w") as f:
                json.dump(serialisable, f)
        except Exception as e:
            logging.warning(f"Failed to save likes to {self._persist_path}: {e}")

    def _load(self) -> None:
        if not self._persist_path or not os.path.exists(self._persist_path):
            return
        try:
            with open(self._persist_path) as f:
                data: dict[str, Any] = json.load(f)
            if isinstance(data, dict):
                self.likes = {user: set(songs) for user, songs in data.items() if isinstance(songs, list)}
                logging.info(f"Loaded likes for {len(self.likes)} user(s)")
        except Exception as e:
            logging.warning(f"Failed to load likes: {e}")
