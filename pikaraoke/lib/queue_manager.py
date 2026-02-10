"""Queue management for PiKaraoke.

Handles song queue operations including enqueueing, editing, clearing,
and fair queue algorithm.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable

from flask_babel import _

from pikaraoke.lib.ffmpeg import get_media_duration
from pikaraoke.lib.file_resolver import FileResolver


class QueueManager:
    """Manages the song queue and queue operations.

    This class handles all queue-related operations including adding songs,
    removing songs, reordering, and implementing fair queue logic.

    Attributes:
        queue: List of queued songs with metadata (user, file, title, semitones).
    """

    def __init__(
        self,
        socketio,
        get_limit_user_songs_by: Callable[[], int],
        get_queue_mode: Callable[[], str] | None = None,
        get_now_playing_user: Callable[[], str | None] | None = None,
        filename_from_path: Callable[[str, bool], str] | None = None,
        log_and_send: Callable[[str, str], None] | None = None,
        get_available_songs: Callable[[], Any] | None = None,
        update_now_playing_socket: Callable[[], None] | None = None,
        skip: Callable[[bool], bool] | None = None,
        get_song_add_cooldown_count: Callable[[], int] | None = None,
        get_song_add_cooldown_duration: Callable[[], int] | None = None,
        get_queue_add_open: Callable[[], bool] | None = None,
        get_queue_closing_time: Callable[[], str | int | None] | None = None,
        get_now_playing_duration: Callable[[], int | None] | None = None,
        get_now_playing_position: Callable[[], float | None] | None = None,
        get_splash_delay: Callable[[], int] | None = None,
        song_add_cooldown_count: int = -1,
        song_add_cooldown_duration: int = -1,
        queue_add_open: bool = True,
        queue_closing_time: str | int | None = None,
        is_admin: Callable[[], bool] | None = None,
    ) -> None:
        """Initialize the QueueManager.

        Args:
            socketio: SocketIO instance for real-time event emission.
            get_limit_user_songs_by: Callback to get max songs per user in queue.
            get_queue_mode: Callback to get queue mode (chronological/democratic/fair).
            get_now_playing_user: Callback to get current playing user.
            filename_from_path: Callback to extract clean filename from path.
            log_and_send: Callback to log and send notifications.
            get_available_songs: Callback to get available songs list.
            update_now_playing_socket: Callback to update now playing state.
            skip: Callback to skip current song.
            get_song_add_cooldown_count: Callback to get cooldown song count.
            get_song_add_cooldown_duration: Callback to get cooldown duration in minutes.
            get_queue_add_open: Callback to check if queue is open for additions.
            get_queue_closing_time: Callback to get queue closing time (unix timestamp).
            get_now_playing_duration: Callback to get current song duration in seconds.
            get_now_playing_position: Callback to get current playback position in seconds.
            get_splash_delay: Callback to get splash delay between songs in seconds.
            song_add_cooldown_count: Number of songs to trigger cooldown (-1 = disabled).
            song_add_cooldown_duration: Cooldown duration in minutes (-1 = disabled).
            queue_add_open: Whether the queue is open for adding songs.
            queue_closing_time: Unix timestamp (seconds) for queue closing time.
            is_admin: Callback to check if the current user is an admin.
        """
        self.queue: list[dict[str, Any]] = []
        self.socketio = socketio
        self._get_limit_user_songs_by = get_limit_user_songs_by
        self._get_queue_mode = get_queue_mode or (lambda: "chronological")
        self._get_now_playing_user = get_now_playing_user
        self._filename_from_path = filename_from_path
        self._log_and_send = log_and_send
        self._get_available_songs = get_available_songs
        self._update_now_playing_socket = update_now_playing_socket
        self._skip = skip
        self._get_song_add_cooldown_count = get_song_add_cooldown_count
        self._get_song_add_cooldown_duration = get_song_add_cooldown_duration
        self._get_queue_add_open = get_queue_add_open
        self._get_queue_closing_time = get_queue_closing_time
        self._get_now_playing_duration = get_now_playing_duration
        self._get_now_playing_position = get_now_playing_position
        self._get_splash_delay = get_splash_delay
        self._is_admin = is_admin
        self.song_add_cooldown_count = song_add_cooldown_count
        self.song_add_cooldown_duration = song_add_cooldown_duration
        self.queue_add_open = queue_add_open
        self.queue_closing_time = queue_closing_time
        # Track user song addition timestamps: {user: [timestamp1, timestamp2, ...]}
        self.user_add_times: dict[str, list[float]] = {}
        # Voting data structure: {song_file_path: {user: vote_value}}
        # vote_value: 1 for upvote, -1 for downvote
        self.votes: dict[str, dict[str, int]] = {}
        # Shadowbanned songs (admin-only) and their temporary user votes
        self.shadowbanned: set[str] = set()
        self.shadowban_votes: dict[str, dict[str, int]] = {}
        self.song_durations: dict[str, int | None] = {}

        # Fair queue playback history
        self.played_users: set[str] = set()
        self.last_played_order: dict[str, int] = {}
        self.play_sequence = 0

    def is_song_in_queue(self, song_path: str) -> bool:
        """Check if a song is already in the queue.

        Args:
            song_path: Path to the song file.

        Returns:
            True if the song is in the queue.
        """
        for each in self.queue:
            if each["file"] == song_path:
                return True
        return False

    def is_user_limited(self, user: str) -> bool:
        """Check if a user has reached their queue limit.
        Args:
            user: Username to check.

        Returns:
            True if the user has reached their song limit.
        """
        limit_user_songs_by = self._get_limit_user_songs_by()
        if limit_user_songs_by == 0 or user == "Pikaraoke" or user == "Randomizer":
            return False

        now_playing_user = self._get_now_playing_user() if self._get_now_playing_user else None
        cont = len([i for i in self.queue if i["user"] == user]) + (
            1 if now_playing_user == user else 0
        )
        return cont >= int(limit_user_songs_by)

    def is_user_in_add_cooldown(self, user: str) -> bool:
        """Check if a user is in song addition cooldown.

        A user is in cooldown if they have added song_add_cooldown_count songs
        within the last song_add_cooldown_duration minutes.

        Args:
            user: Username to check.

        Returns:
            True if the user is in cooldown, False otherwise.
        """
        cooldown_count = (
            self._get_song_add_cooldown_count()
            if self._get_song_add_cooldown_count
            else self.song_add_cooldown_count
        )
        cooldown_duration = (
            self._get_song_add_cooldown_duration()
            if self._get_song_add_cooldown_duration
            else self.song_add_cooldown_duration
        )
        # Cooldown disabled if either value is -1
        if cooldown_count == -1 or cooldown_duration == -1:
            return False

        # Admin users are never in cooldown
        if self._is_admin and self._is_admin():
            return False

        # Special users are never in cooldown
        if user == "Pikaraoke" or user == "Randomizer":
            return False

        current_time = time.time()
        cutoff_time = current_time - (cooldown_duration * 60)

        # Get user's add times, filtering out old entries
        if user not in self.user_add_times:
            self.user_add_times[user] = []

        # Remove timestamps older than the cutoff
        self.user_add_times[user] = [t for t in self.user_add_times[user] if t > cutoff_time]

        # Check if user has added enough songs within the cooldown window
        return len(self.user_add_times[user]) >= cooldown_count

    def _get_queue_add_open_value(self) -> bool:
        if self._get_queue_add_open is not None:
            return bool(self._get_queue_add_open())
        return bool(self.queue_add_open)

    def _get_queue_closing_time_value(self) -> str | int | None:
        if self._get_queue_closing_time is not None:
            return self._get_queue_closing_time()
        return self.queue_closing_time

    def _get_closing_timestamp(self) -> int | None:
        closing_time = self._get_queue_closing_time_value()
        if closing_time is None or closing_time == "":
            return None
        if isinstance(closing_time, int):
            return closing_time
        if isinstance(closing_time, float):
            return int(closing_time)
        if isinstance(closing_time, str) and closing_time.isdigit():
            return int(closing_time)
        return None

    def _format_closing_time_display(self, closing_ts: int | None) -> str | None:
        if not closing_ts:
            return None
        return time.strftime("%H:%M", time.localtime(closing_ts))

    def _get_now_playing_remaining(self) -> int:
        if self._get_now_playing_duration is None:
            return 0
        duration = self._get_now_playing_duration()
        if duration is None:
            return 0
        position = self._get_now_playing_position() if self._get_now_playing_position else None
        if position is None:
            return max(0, duration)
        return max(0, duration - int(position))

    def _get_splash_delay_value(self) -> int:
        if self._get_splash_delay is not None:
            return int(self._get_splash_delay() or 0)
        return 0

    def get_queue_add_status(self) -> dict[str, Any]:
        if self._is_admin and self._is_admin():
            return {
                "is_open": True,
                "reason": None,
                "closing_time": self._format_closing_time_display(self._get_closing_timestamp()),
            }

        if not self._get_queue_add_open_value():
            return {
                "is_open": False,
                "reason": "manual",
                "closing_time": self._format_closing_time_display(self._get_closing_timestamp()),
            }

        now_ts = time.time()
        closing_ts = self._get_closing_timestamp()
        if closing_ts:
            current_end_offset = self._estimate_queue_end_offset_seconds(extra_song_path=None)
            if current_end_offset is not None:
                current_end_ts = now_ts + current_end_offset
                if current_end_ts > closing_ts:
                    return {
                        "is_open": False,
                        "reason": "time",
                        "closing_time": self._format_closing_time_display(closing_ts),
                    }

        return {
            "is_open": True,
            "reason": None,
            "closing_time": self._format_closing_time_display(closing_ts),
        }

    def get_queue_add_block_message(self) -> str:
        status = self.get_queue_add_status()
        if status["is_open"]:
            return ""

        if status["reason"] == "time" and status.get("closing_time"):
            return (
                _("Queue is closed. No more songs can be added after %s.") % status["closing_time"]
            )

        return _("Queue is closed. No more songs can be added right now.")

    def _estimate_queue_end_offset_seconds(self, extra_song_path: str | None = None) -> int | None:
        splash_delay = self._get_splash_delay_value()
        remaining = self._get_now_playing_remaining()
        offset = remaining

        total_queue_len = len(self.queue) + (1 if extra_song_path else 0)
        # Only add splash delay if there are queued songs (not just the song being added)
        if len(self.queue) > 0:
            offset += splash_delay

        for idx, item in enumerate(self.queue):
            duration = self.get_song_duration(item["file"])
            if duration is None:
                return None
            offset += duration

            has_more = idx < (len(self.queue) - 1) or extra_song_path is not None
            if has_more:
                offset += splash_delay

        if extra_song_path:
            extra_duration = self.get_song_duration(extra_song_path)
            if extra_duration is None:
                return None
            offset += extra_duration

        return offset

    def can_add_song_before_closing(self, song_path: str) -> tuple[bool, str | None, int | None]:
        if self._is_admin and self._is_admin():
            return (True, None, None)

        closing_ts = self._get_closing_timestamp()
        if not closing_ts:
            return (True, None, None)

        now_ts = time.time()
        current_end_offset = self._estimate_queue_end_offset_seconds(extra_song_path=None)
        if current_end_offset is None:
            return (
                False,
                _("Cannot add songs because queue timing cannot be estimated."),
                closing_ts,
            )

        new_end_offset = self._estimate_queue_end_offset_seconds(extra_song_path=song_path)
        if new_end_offset is None:
            return (
                False,
                _("Cannot add this song because its duration is unknown."),
                closing_ts,
            )

        current_end_ts = now_ts + current_end_offset
        if current_end_ts > closing_ts:
            return (
                False,
                _("Queue is closed. The queue already ends after %s."),
                closing_ts,
            )

        new_end_ts = now_ts + new_end_offset
        if new_end_ts > closing_ts:
            return (
                False,
                _("Queue is closed. This song would end after %s."),
                closing_ts,
            )

        return (True, None, None)

    def get_song_duration(self, song_path: str) -> int | None:
        if song_path in self.song_durations:
            return self.song_durations[song_path]

        duration = get_media_duration(song_path)
        if duration is None:
            try:
                resolver = FileResolver(song_path)
                duration = resolver.duration
            except Exception:
                duration = None

        self.song_durations[song_path] = duration
        return duration

    def _calculate_fair_queue_position(self, user: str) -> int:
        """Calculate insertion position for round-robin fair queuing.

        Implements Nagle Fair Queuing: users take turns in rounds. A user's Nth
        song is placed after all other users' Nth songs (or at queue end).

        Args:
            user: Username adding the song.

        Returns:
            Queue index where the song should be inserted.
        """
        # Count how many songs this user already has in queue
        user_song_count = sum(1 for item in self.queue if item["user"] == user)

        # Find position after the last song in "round N" where N = user_song_count
        # Round 0 = first song from each user, Round 1 = second song, etc.
        target_round = user_song_count
        songs_seen_per_user: dict[str, int] = {}

        for idx, item in enumerate(self.queue):
            queue_user = item["user"]
            songs_seen_per_user[queue_user] = songs_seen_per_user.get(queue_user, 0) + 1
            # This song is in round (count - 1) for its user
            song_round = songs_seen_per_user[queue_user] - 1
            if song_round == target_round:
                # Found a song in the target round, insert after it
                # Keep scanning to find the LAST song in this round
                pass
            elif song_round > target_round:
                # We've moved past target round, insert here
                return idx

        # All songs are in rounds <= target_round, append to end
        return len(self.queue)

    def enqueue(
        self,
        song_path: str,
        user: str = "Pikaraoke",
        semitones: int = 0,
        add_to_front: bool = False,
        log_action: bool = True,
        bypass_queue_restrictions: bool = False,
    ) -> bool | list[bool | str]:
        """Add a song to the queue.

        Args:
            song_path: Path to the song file.
            user: Username adding the song.
            semitones: Transpose value for playback.
            add_to_front: If True, add to front of queue instead of back.
            log_action: Whether to log and notify about the action.
            bypass_queue_restrictions: If True, bypass queue open/closing checks (e.g., for downloads).

        Returns:
            False if song already in queue, or list of [success, message].
        """
        if not bypass_queue_restrictions and not (self._is_admin and self._is_admin()):
            if not self._get_queue_add_open_value():
                return [False, self.get_queue_add_block_message()]
            can_add, reason, closing_ts = self.can_add_song_before_closing(song_path)
            if not can_add:
                return [False, reason or self.get_queue_add_block_message(), closing_ts]

        if self.is_song_in_queue(song_path):
            logging.warning("Song is already in queue, will not add: " + song_path)
            return False
        elif not bypass_queue_restrictions and self.is_user_limited(user):
            limit = self._get_limit_user_songs_by()
            logging.debug("User limited by: " + str(limit))
            return [
                False,
                _("You reached the limit of %s song(s) from an user in queue!") % (str(limit)),
            ]
        elif not bypass_queue_restrictions and self.is_user_in_add_cooldown(user):
            logging.debug(f"User {user} is in song addition cooldown")
            return [
                False,
                _("You are adding songs too quickly. Please wait before adding another song."),
            ]
        else:
            if self._filename_from_path:
                title = self._filename_from_path(song_path, True)
            else:
                title = song_path

            # Track this song addition timestamp for cooldown purposes
            if user not in self.user_add_times:
                self.user_add_times[user] = []
            self.user_add_times[user].append(time.time())

            queue_item = {
                "user": user,
                "file": song_path,
                "title": title,
                "semitones": semitones,
            }
            if add_to_front:
                if self._log_and_send:
                    # MSG: Message shown after the song is added to the top of the queue
                    self._log_and_send(
                        _("%s added to top of queue: %s") % (user, queue_item["title"]), "info"
                    )
                self.queue.insert(0, queue_item)
            else:
                if log_action and self._log_and_send:
                    # MSG: Message shown after the song is added to the queue
                    self._log_and_send(
                        _("%s added to the queue: %s") % (user, queue_item["title"]), "info"
                    )
                if self._get_queue_mode() == "fair":
                    self.queue.append(queue_item)
                    self._reorder_queue_fair()
                else:
                    self.queue.append(queue_item)
                    if self._get_queue_mode() == "democratic":
                        self._reorder_queue_by_votes()
            self.update_queue_socket()
            if self._update_now_playing_socket:
                self._update_now_playing_socket()
            return [
                True,
                _("Song added to the queue: %s") % title,
            ]

    def queue_add_random(self, amount: int) -> bool:
        """Add random songs to the queue.

        Args:
            amount: Number of random songs to add.

        Returns:
            True if successful, False if ran out of songs.
        """
        logging.info("Adding %d random songs to queue" % amount)

        if not self._get_available_songs:
            logging.error("No available songs callback provided!")
            return False

        available_songs = self._get_available_songs()

        if len(available_songs) == 0:
            logging.warning("No available songs!")
            return False

        # Get songs not already in queue
        queued_paths = {item["file"] for item in self.queue}
        eligible_songs = [s for s in available_songs if s not in queued_paths]

        if len(eligible_songs) == 0:
            logging.warning("All songs are already in queue!")
            return False

        # Sample up to 'amount' songs (or all eligible if fewer available)
        sample_size = min(amount, len(eligible_songs))
        # Explicitly seed with current time to ensure true randomness across restarts
        random.seed()
        selected = random.sample(eligible_songs, sample_size)

        for song in selected:
            self.enqueue(song, "Randomizer")
        if sample_size < amount:
            logging.warning("Ran out of songs! Only added %d" % sample_size)
            return False

        return True

    def queue_clear(self) -> None:
        """Clear all songs from the queue and skip current song."""
        if self._log_and_send:
            # MSG: Message shown after the queue is cleared
            self._log_and_send(_("Clear queue"), "danger")
        self.queue = []
        self.votes.clear()
        self.shadowbanned.clear()
        self.shadowban_votes.clear()
        self.update_queue_socket()
        if self._update_now_playing_socket:
            self._update_now_playing_socket()
        if self._skip:
            self._skip(False)

    def queue_edit(self, song_name: str, action: str) -> bool:
        """Edit the queue by moving or removing a song.

        Args:
            song_name: Name/path of the song to edit.
            action: Action to perform ('up', 'down', 'top', 'delete').

        Returns:
            True if the action was successful.
        """
        index = 0
        song = None
        rc = False
        for each in self.queue:
            if song_name in each["file"]:
                song = each
                break
            else:
                index += 1
        if song is None:
            logging.error("Song not found in queue: " + song_name)
            return rc
        if action == "top":
            if index == 0:
                logging.warning("Song is already at top of queue: " + song["file"])
                rc = True  # Still return True since it's already where we want it
            else:
                logging.info("Moving song to top of queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(0, song)
                rc = True
        elif action == "up":
            if index < 1:
                logging.warning("Song is up next, can't bump up in queue: " + song["file"])
            else:
                logging.info("Bumping song up in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index - 1, song)
                rc = True
        elif action == "down":
            if index == len(self.queue) - 1:
                logging.warning("Song is already last, can't bump down in queue: " + song["file"])
            else:
                logging.info("Bumping song down in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index + 1, song)
                rc = True
        elif action == "delete":
            logging.info("Deleting song from queue: " + song["file"])
            self.clear_song_votes(song["file"])
            del self.queue[index]
            rc = True
        else:
            logging.error("Unrecognized direction: " + action)
        if rc:
            self.update_queue_socket()
            if self._update_now_playing_socket:
                self._update_now_playing_socket()
        return rc

    def update_queue_socket(self) -> None:
        """Emit queue_update state change via SocketIO."""
        if self.socketio:
            self.socketio.emit("queue_update", namespace="/")

    def is_shadowbanned(self, song_file: str) -> bool:
        """Check if a song is shadowbanned."""
        return song_file in self.shadowbanned

    def toggle_shadowban(self, song_file: str) -> bool:
        """Toggle shadowban status for a song.

        Returns:
            True if song is now shadowbanned, False otherwise.
        """
        if song_file in self.shadowbanned:
            self.shadowbanned.remove(song_file)
            if song_file in self.shadowban_votes:
                del self.shadowban_votes[song_file]
            return False
        self.shadowbanned.add(song_file)
        return True

    def get_shadowban_base_rating(self) -> int:
        """Get the base rating for shadowbanned songs.

        Uses the worst (lowest) rating among non-shadowbanned songs.
        Returns 0 if no non-shadowbanned songs exist.
        """
        ratings = [
            self.get_song_rating(item["file"])
            for item in self.queue
            if not self.is_shadowbanned(item["file"])
        ]
        return min(ratings) if ratings else 0

    def get_shadowban_user_vote(self, song_file: str, user: str) -> int:
        """Get a user's temporary vote for a shadowbanned song."""
        if song_file not in self.shadowban_votes:
            return 0
        return self.shadowban_votes[song_file].get(user, 0)

    def get_display_rating(
        self, song_file: str, user: str | None = None, base_rating: int | None = None
    ) -> int:
        """Get the rating to display for a song.

        Shadowbanned songs show the worst rating in the queue; the current user's
        temporary vote is added only for that user.
        """
        if not self.is_shadowbanned(song_file):
            return self.get_song_rating(song_file)

        effective_base = (
            base_rating if base_rating is not None else self.get_shadowban_base_rating()
        )
        if user:
            return effective_base + self.get_shadowban_user_vote(song_file, user)
        return effective_base

    def peek_next_song(self) -> dict[str, Any] | None:
        """Get the next song to play, skipping shadowbanned entries when possible."""
        if not self.queue:
            return None
        if self._get_queue_mode() == "fair":
            fair_next = self._get_fair_next_song()
            if fair_next:
                return fair_next
        for item in self.queue:
            if not self.is_shadowbanned(item["file"]):
                return item
        return self.queue[0]

    def _get_fair_next_song(self) -> dict[str, Any] | None:
        """Select next song using fair queue playback history rules."""
        candidates = self._get_fair_candidates()
        if not candidates:
            return None

        # Prefer the first song whose user has never played before
        for item in candidates:
            user = (item.get("user") or "").strip()
            if not user or user not in self.played_users:
                return item

        # Otherwise, pick the user whose last play was the longest ago
        oldest_item = None
        oldest_order = None
        for item in candidates:
            user = (item.get("user") or "").strip()
            order = self.last_played_order.get(user, 0)
            if oldest_order is None or order < oldest_order:
                oldest_item = item
                oldest_order = order
        return oldest_item

    def _get_fair_candidates(self) -> list[dict[str, Any]]:
        """Return fair queue candidates, ignoring shadowbanned songs when possible."""
        if not self.queue:
            return []
        non_shadowbanned = [item for item in self.queue if not self.is_shadowbanned(item["file"])]
        return non_shadowbanned if non_shadowbanned else list(self.queue)

    def _reorder_queue_fair(self) -> None:
        """Reorder queue according to fair-queue playback history rules."""
        if not self.queue:
            return

        non_shadowbanned = [item for item in self.queue if not self.is_shadowbanned(item["file"])]
        shadowbanned = [item for item in self.queue if self.is_shadowbanned(item["file"])]
        candidates = non_shadowbanned if non_shadowbanned else list(self.queue)

        temp_played_users = set(self.played_users)
        temp_last_played = dict(self.last_played_order)
        temp_sequence = self.play_sequence

        ordered: list[dict[str, Any]] = []
        remaining = list(candidates)

        while remaining:
            chosen_index = None
            for idx, item in enumerate(remaining):
                user = (item.get("user") or "").strip()
                if not user or user not in temp_played_users:
                    chosen_index = idx
                    break

            if chosen_index is None:
                oldest_order = None
                for idx, item in enumerate(remaining):
                    user = (item.get("user") or "").strip()
                    order = temp_last_played.get(user, 0)
                    if oldest_order is None or order < oldest_order:
                        oldest_order = order
                        chosen_index = idx

            if chosen_index is None:
                break

            chosen = remaining.pop(chosen_index)
            ordered.append(chosen)

            user = (chosen.get("user") or "").strip()
            if user:
                temp_sequence += 1
                temp_played_users.add(user)
                temp_last_played[user] = temp_sequence

        if non_shadowbanned:
            self.queue = ordered + shadowbanned
        else:
            self.queue = ordered

        self.update_queue_socket()
        if self._update_now_playing_socket:
            self._update_now_playing_socket()

    def record_play(self, user: str | None) -> None:
        """Record that a user's song has been played for fair-queue selection."""
        if not user:
            return
        self.play_sequence += 1
        self.played_users.add(user)
        self.last_played_order[user] = self.play_sequence
        if self._get_queue_mode() == "fair":
            self._reorder_queue_fair()

    def pop_song_by_file(self, song_file: str) -> dict[str, Any] | None:
        """Remove and return a song entry by file path."""
        for idx, item in enumerate(self.queue):
            if item["file"] == song_file:
                return self.queue.pop(idx)
        return None

    def vote_song(self, song_file: str, user: str, vote_type: str) -> dict[str, Any]:
        """Record a vote for a song (upvote or downvote).

        Each user can only vote once per song. Voting again removes the previous vote and applies
        the new one.

        Args:
            song_file: Path to the song file.
            user: Username casting the vote.
            vote_type: Either "upvote" or "downvote".

        Returns:
            Dictionary with success status and net vote count.
        """
        if vote_type not in ("upvote", "downvote"):
            return {"success": False, "error": "Invalid vote type"}

        vote_value = 1 if vote_type == "upvote" else -1

        if self.is_shadowbanned(song_file):
            if song_file not in self.shadowban_votes:
                self.shadowban_votes[song_file] = {}

            user_votes = self.shadowban_votes[song_file]
            previous_vote = user_votes.get(user, 0)

            if previous_vote != 0 and previous_vote == vote_value:
                del user_votes[user]
            else:
                user_votes[user] = vote_value

            net_votes = self.get_display_rating(song_file, user)

            logging.debug(
                f"Shadowban vote recorded: {user} {vote_type}d {song_file} (net: {net_votes})"
            )

            return {
                "success": True,
                "net_votes": net_votes,
                "user_vote": user_votes.get(user, 0),
            }

        # Initialize votes dict for this song if needed
        if song_file not in self.votes:
            self.votes[song_file] = {}

        # Check if user already voted on this song
        user_votes = self.votes[song_file]
        previous_vote = user_votes.get(user, 0)

        # If user is changing from one vote type to another or removing their vote
        if previous_vote != 0 and previous_vote == vote_value:
            # User is clicking the same button - remove their vote
            del user_votes[user]
        else:
            # Record the new vote (replaces any previous vote)
            user_votes[user] = vote_value

        # Calculate net vote count
        net_votes = self.get_song_rating(song_file)

        logging.debug(f"Vote recorded: {user} {vote_type}d {song_file} (net: {net_votes})")

        # Reorder queue based on votes only when voting is enabled and fair queue is disabled
        if self._get_queue_mode() != "fair" and self._get_queue_mode() == "democratic":
            self._reorder_queue_by_votes()

        return {
            "success": True,
            "net_votes": net_votes,
            "user_vote": user_votes.get(user, 0),
        }

    def _reorder_queue_by_votes(self) -> None:
        """Reorder queue by vote score (descending), stable for equal scores."""
        if not self.queue:
            return

        shadowban_base = self.get_shadowban_base_rating()
        scored = []
        for idx, item in enumerate(self.queue):
            is_shadowbanned = self.is_shadowbanned(item["file"])
            rating = shadowban_base if is_shadowbanned else self.get_song_rating(item["file"])
            scored.append((is_shadowbanned, rating, idx, item))

        scored.sort(key=lambda x: (x[0], -x[1], x[2]))
        self.queue = [item for _, __, ___, item in scored]
        self.update_queue_socket()
        if self._update_now_playing_socket:
            self._update_now_playing_socket()

    def get_song_rating(self, song_file: str) -> int:
        """Get the net rating (upvotes - downvotes) for a song.

        Args:
            song_file: Path to the song file.

        Returns:
            Net vote count (can be positive, negative, or zero).
        """
        if song_file not in self.votes:
            return 0

        votes = self.votes[song_file]
        return sum(votes.values())

    def get_user_vote(self, song_file: str, user: str) -> int:
        """Get the current vote for a user on a specific song.

        Args:
            song_file: Path to the song file.
            user: Username to check.

        Returns:
            1 for upvote, -1 for downvote, 0 for no vote.
        """
        if self.is_shadowbanned(song_file):
            return self.get_shadowban_user_vote(song_file, user)
        if song_file not in self.votes:
            return 0
        return self.votes[song_file].get(user, 0)

    def clear_song_votes(self, song_file: str) -> None:
        """Clear all votes for a song (e.g., when it's removed from queue).

        Args:
            song_file: Path to the song file.
        """
        if song_file in self.votes:
            del self.votes[song_file]
        if song_file in self.shadowban_votes:
            del self.shadowban_votes[song_file]
        if song_file in self.shadowbanned:
            self.shadowbanned.remove(song_file)
