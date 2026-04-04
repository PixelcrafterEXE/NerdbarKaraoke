"""Download queue manager for serialized video downloads."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import uuid
from queue import Queue
from threading import Thread
from typing import TYPE_CHECKING

from pikaraoke.lib.youtube_dl import (
    _DNS_RETRY_COUNT,
    _DNS_RETRY_DELAY,
    _flush_dns_cache,
    _is_dns_error,
    build_ytdl_download_command,
    get_youtube_id_from_url,
)


def _broadcast_helper(app, event):
    """Helper to broadcast event with app context if available."""
    from pikaraoke.lib.current_app import broadcast_event

    if app:
        with app.app_context():
            broadcast_event(event)
    else:
        broadcast_event(event)


if TYPE_CHECKING:
    from pikaraoke.karaoke import Karaoke


class DownloadManager:
    """Manages a queue of video downloads, processing them serially.

    This prevents rate limiting from download sources and reduces CPU load
    by ensuring only one download runs at a time.

    Attributes:
        download_queue: Queue holding pending download requests.
    """

    def __init__(self, karaoke: Karaoke) -> None:
        """Initialize the download manager.

        Args:
            karaoke: Reference to the Karaoke instance for config and callbacks.
        """
        self.karaoke = karaoke
        self.download_queue: Queue = Queue()
        self.pending_downloads: list[dict] = []  # Shadow queue for visibility
        self.download_errors: list[dict] = []  # Track failed downloads
        self.active_download: dict | None = None
        self._worker_thread: Thread | None = None
        self._is_downloading: bool = False  # Track if a download is currently in progress
        self.app = None  # Flask app instance for background context

    def start(self) -> None:
        """Start the download worker thread."""
        self._worker_thread = Thread(target=self._process_queue, daemon=True)
        self._worker_thread.start()
        logging.debug("Download queue worker started")

    def get_downloads_status(self) -> dict:
        """Get the status of active and pending downloads.

        Returns:
            Dict containing 'active' download info and list of 'pending' downloads.
        """
        return {
            "active": self.active_download,
            "pending": self.pending_downloads,
            "errors": self.download_errors,
        }

    def remove_error(self, error_id: str) -> bool:
        """Remove an error from the list by ID.

        Args:
            error_id: The ID of the error to remove.

        Returns:
            True if removed, False if not found.
        """
        initial_len = len(self.download_errors)
        self.download_errors = [e for e in self.download_errors if e["id"] != error_id]
        return len(self.download_errors) < initial_len

    def queue_download(
        self,
        video_url: str,
        enqueue: bool = False,
        user: str = "Pikaraoke",
        title: str | None = None,
    ) -> None:
        """Queue a video for download.

        Downloads are processed serially to prevent rate limiting and CPU overload.
        A notification is sent when the download is queued, and another when it starts.

        Args:
            video_url: YouTube video URL.
            enqueue: Whether to add to playback queue after download.
            user: Username to attribute the download to.
            title: Display title (defaults to URL if not provided).
        """
        from flask_babel import _

        displayed_title = title if title else video_url

        # Check how many items are ahead (in queue + currently downloading)
        pending_count = self.download_queue.qsize() + (1 if self._is_downloading else 0)

        if pending_count > 0:
            # MSG: Message shown when download is added to queue (not first in line)
            self.karaoke.log_and_send(
                _("Download queued (#%d): %s") % (pending_count + 1, displayed_title)
            )
        else:
            # MSG: Message shown when download is added and will start immediately
            self.karaoke.log_and_send(_("Download starting: %s") % displayed_title)

        # If queue was just started (was not downloading before), emit event
        if not self._is_downloading and self.download_queue.empty():
            _broadcast_helper(self.app, "download_started")

        download_data = {
            "video_url": video_url,
            "enqueue": enqueue,
            "user": user,
            "title": title,
            "display_title": displayed_title,
        }

        # Add to the download queue and shadow list
        self.download_queue.put(download_data)
        self.pending_downloads.append(download_data)

    def _process_queue(self) -> None:
        """Worker thread that processes downloads from the queue serially.

        Runs indefinitely, blocking on queue.get() until items are available.
        Each download is processed completely before the next one starts.
        Handles both YouTube downloads and local file transcoding.
        """
        while True:
            download_request = self.download_queue.get()

            # Remove from shadow queue
            # Note: Since this is a single worker thread and append happens on main thread,
            # we simply pop the first item as it corresponds to FIFO queue.
            # In a multi-worker scenario, this would need a lock.
            if self.pending_downloads:
                self.pending_downloads.pop(0)

            self._is_downloading = True

            # Check if this is a transcoding request or a download request
            is_transcode = download_request.get("is_transcode", False)

            try:
                if is_transcode:
                    # Initialize active transcode state
                    self.active_download = {
                        "title": download_request.get("display_title", "Transcoding"),
                        "file": download_request["file_path"],
                        "user": download_request["user"],
                        "progress": 0.0,
                        "status": "starting",
                    }

                    self._execute_transcode(
                        download_request["file_path"],
                        download_request["enqueue"],
                        download_request["user"],
                        download_request["title"],
                    )
                else:
                    # Initialize active download state
                    self.active_download = {
                        "title": download_request.get("display_title", download_request["video_url"]),
                        "url": download_request["video_url"],
                        "user": download_request["user"],
                        "progress": 0.0,
                        "status": "starting",
                        "eta": "--:--",
                        "speed": "---",
                    }

                    self._execute_download(
                        download_request["video_url"],
                        download_request["enqueue"],
                        download_request["user"],
                        download_request["title"],
                    )
            except Exception as e:
                logging.error(f"Error processing request: {e}")
            finally:
                self._is_downloading = False
                self.active_download = None
                self.download_queue.task_done()

                # Check if we are done with all downloads
                if self.download_queue.empty():
                    _broadcast_helper(self.app, "download_stopped")

    def _format_eta(self, value: str) -> str:
        """Format seconds or already-formatted time strings into mm:ss or hh:mm:ss."""
        v = value.strip()
        if not v or v.lower() in {"none", "na", "n/a", "unknown"}:
            return "--:--"
        if ":" in v:
            return v
        try:
            seconds = int(float(v))
        except ValueError:
            return v
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        if minutes > 0:
            return f"{minutes:02d}:{secs:02d}"
        return f"{secs}s"

    def _format_speed(self, value: str) -> str:
        """Format numeric speed in B/s to human-readable units."""
        v = value.strip()
        if not v or v.lower() in {"none", "na", "n/a", "unknown"}:
            return "---"
        if any(u in v for u in ("KiB/s", "MiB/s", "GiB/s", "KB/s", "MB/s", "GB/s")):
            return v
        try:
            bps = float(v)
        except ValueError:
            return v
        units = ["B/s", "KiB/s", "MiB/s", "GiB/s", "TiB/s"]
        idx = 0
        while bps >= 1024 and idx < len(units) - 1:
            bps /= 1024
            idx += 1
        return f"{bps:.2f} {units[idx]}"

    def _execute_download(
        self,
        video_url: str,
        enqueue: bool,
        user: str,
        title: str | None,
    ) -> int:
        """Execute a video download.

        Args:
            video_url: YouTube video URL.
            enqueue: Whether to add to queue after download.
            user: Username to attribute the download to.
            title: Display title (defaults to URL if not provided).

        Returns:
            Return code from the download process (0 = success).
        """
        from flask_babel import _

        k = self.karaoke
        displayed_title = title if title else video_url

        # MSG: Message shown when download actually starts (after waiting in queue)
        k.log_and_send(_("Downloading video: %s") % displayed_title)

        cmd = build_ytdl_download_command(
            video_url,
            k.download_path,
            k.high_quality,
            k.youtubedl_proxy,
            k.additional_ytdl_args,
        )
        logging.debug("Youtube-dl command: " + " ".join(cmd))

        # Use Popen to capture output in real-time
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")

        # Retry loop for DNS failures (issue #7): stale DNS cache in
        # long-running Docker containers can cause resolution errors.
        for dns_attempt in range(_DNS_RETRY_COUNT):
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # Line buffered
                universal_newlines=True,
                env=env,
            )

            output_buffer = []
            video_id = get_youtube_id_from_url(video_url)

            # Parse pipe-delimited progress format from yt-dlp: downloaded|total|total_est|speed|eta|percent
            # Example: 1024|791367|NA|345393.43|0|NA
            # Note: percent field is typically "NA", so we calculate it from downloaded/total
            progress_regex = re.compile(r"^(\d+)\|(\d+)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)$")

            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    output_buffer.append(line)
                    line_stripped = line.strip()
                    if self.active_download and "|" in line_stripped:
                        match = progress_regex.match(line_stripped)
                        if match:
                            try:
                                downloaded = int(match.group(1))
                                total = int(match.group(2))
                                total_est = match.group(3)  # Can be "NA" or numeric
                                speed_raw = match.group(4)
                                eta_raw = match.group(5)
                                percent_str = match.group(6)

                                # Calculate percent from downloaded/total
                                percent = 0.0
                                if total > 0:
                                    percent = (downloaded / total) * 100.0
                                percent = min(100.0, max(0.0, percent))

                                self.active_download["progress"] = percent
                                self.active_download["status"] = "downloading"
                                self.active_download["speed"] = self._format_speed(speed_raw)
                                self.active_download["eta"] = self._format_eta(eta_raw)
                            except (ValueError, AttributeError) as e:
                                logging.warning(f"Progress parsing error: {e}, line: {line_stripped}")

            rc = process.poll()
            output = "".join(output_buffer)

            if rc != 0:
                # Check for DNS errors and retry (issue #7)
                if _is_dns_error(output) and dns_attempt < _DNS_RETRY_COUNT - 1:
                    import time
                    logging.warning(
                        f"DNS resolution failed during download (attempt {dns_attempt + 1}/"
                        f"{_DNS_RETRY_COUNT}), flushing DNS cache and retrying in "
                        f"{_DNS_RETRY_DELAY}s..."
                    )
                    _flush_dns_cache()
                    time.sleep(_DNS_RETRY_DELAY)
                    if self.active_download:
                        self.active_download["status"] = "retrying"
                        self.active_download["progress"] = 0
                    continue  # Retry the download

                # Non-DNS error or final retry exhausted
                k.log_and_send(_("Error downloading song: ") + displayed_title, "danger")
                logging.error(f"yt-dlp stderr: {output}")
                self.download_errors.append(
                    {
                        "id": str(uuid.uuid4()),
                        "title": displayed_title,
                        "url": video_url,
                        "user": user,
                        "error": output or "Unknown error",
                    }
                )
            else:
                if self.active_download:
                    self.active_download["progress"] = 100
                    self.active_download["status"] = "complete"

                if enqueue:
                    # MSG: Message shown after the download is completed and queued
                    k.log_and_send(_("Downloaded and queued: %s") % displayed_title, "success")
                else:
                    # MSG: Message shown after the download is completed but not queued
                    k.log_and_send(_("Downloaded: %s") % displayed_title, "success")

                # After download, find the file path by ID
                song_path = None
                if video_id:
                    logging.debug(f"Searching for downloaded file by ID: {video_id}")
                    song_path = k.available_songs.find_by_id(k.download_path, video_id)
                else:
                    logging.warning("No video ID available to find downloaded song")

                song_is_valid = False
                if song_path:
                    song_is_valid = k.available_songs.add_if_valid(song_path)
                else:
                    logging.warning(
                        f"Could not find downloaded song in {k.download_path} matching ID: {video_id}"
                    )

                if enqueue:
                    if song_is_valid:
                        bypass = False
                        if k.queue_manager._is_admin and k.queue_manager._is_admin():
                            bypass = True
                        k.queue_manager.enqueue(
                            song_path,
                            user,
                            log_action=False,
                            bypass_queue_restrictions=bypass,
                        )
                    else:
                        # MSG: Message shown after the download is completed but the adding to queue fails
                        k.log_and_send(_("Error queueing song: ") + displayed_title, "danger")

                # Download succeeded, break out of retry loop
                break

        return rc

    def queue_transcode_file(
        self,
        file_path: str,
        enqueue: bool = False,
        user: str = "Pikaraoke",
        title: str | None = None,
    ) -> None:
        """Queue a local video file for transcoding.

        Similar to queue_download but processes local files instead of downloading.
        Files are transcoded to MP4 format and saved in the songs folder.

        Args:
            file_path: Path to the video file to transcode.
            enqueue: Whether to add to playback queue after transcoding.
            user: Username to attribute the transcoding to.
            title: Display title (defaults to filename if not provided).
        """
        from flask_babel import _

        # Extract filename for display
        filename = os.path.basename(file_path)
        displayed_title = title if title else os.path.splitext(filename)[0]

        # Check how many items are ahead (in queue + currently downloading)
        pending_count = self.download_queue.qsize() + (1 if self._is_downloading else 0)

        if pending_count > 0:
            # MSG: Message shown when transcode is added to queue (not first in line)
            self.karaoke.log_and_send(
                _("Transcoding queued (#%d): %s") % (pending_count + 1, displayed_title)
            )
        else:
            # MSG: Message shown when transcode is added and will start immediately
            self.karaoke.log_and_send(_("Transcoding starting: %s") % displayed_title)

        # If queue was just started (was not downloading before), emit event
        if not self._is_downloading and self.download_queue.empty():
            _broadcast_helper(self.app, "download_started")

        transcode_data = {
            "file_path": file_path,
            "enqueue": enqueue,
            "user": user,
            "title": title,
            "display_title": displayed_title,
            "is_transcode": True,  # Flag to distinguish from downloads
        }

        # Add to the download queue and shadow list
        self.download_queue.put(transcode_data)
        self.pending_downloads.append(transcode_data)

    def _execute_transcode(
        self,
        file_path: str,
        enqueue: bool,
        user: str,
        title: str | None,
    ) -> int:
        """Execute transcoding of a video file to MP4 format."""
        from flask_babel import _

        k = self.karaoke
        filename = os.path.basename(file_path)
        displayed_title = title if title else os.path.splitext(filename)[0]

        k.log_and_send(_("Transcoding file: %s") % displayed_title)

        if self.active_download:
            self.active_download["progress"] = 0
            self.active_download["status"] = "transcoding"

        try:
            # Generate unique output filename
            output_path = os.path.join(k.download_path, f"{displayed_title}.mp4")
            counter = 1
            while os.path.exists(output_path):
                output_path = os.path.join(k.download_path, f"{displayed_title}_{counter}.mp4")
                counter += 1

            # Check if input has audio stream
            has_audio = False
            try:
                probe_result = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "default=noprint_wrappers=1:nokey=1", file_path],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                has_audio = probe_result.stdout.strip() == "audio"
            except Exception as e:
                logging.warning(f"Could not probe audio stream: {e}")
            
            if not has_audio:
                # For video-only files, generate silence to match video duration
                try:
                    duration_result = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    video_duration = float(duration_result.stdout.strip())
                    # Create filter string with exact duration
                    audio_input = f"anullsrc=channel_layout=stereo:sample_rate=48000[a];[a]atrim=duration={video_duration}"
                except (ValueError, subprocess.TimeoutExpired):
                    # Fallback: use standard anullsrc without trimming (may create very long audio)
                    logging.warning("Could not get video duration, using standard silent audio")
                    audio_input = "anullsrc=channel_layout=stereo:sample_rate=48000"
                
                cmd = [
                    "ffmpeg", "-nostdin",
                    "-i", file_path,
                    "-f", "lavfi", "-i", audio_input,
                    "-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-movflags", "+faststart", "-f", "mp4", "-y",
                    output_path,
                ]
            else:
                # For files with audio, standard transcode
                cmd = [
                    "ffmpeg",
                    "-nostdin",
                    "-i", file_path,
                    "-c:v", "libx264",
                    "-profile:v", "high",
                    "-preset", "medium",
                    "-crf", "23",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-ar", "48000",
                    "-ac", "2",
                    "-movflags", "+faststart",
                    "-f", "mp4",
                    "-y",
                    output_path,
                ]

            logging.debug("Transcode command: " + " ".join(cmd))

            # Use Popen to capture output in real-time
            env = os.environ.copy()
            env.setdefault("PYTHONUNBUFFERED", "1")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
                universal_newlines=True,
                env=env,
            )

            output_buffer = []
            stderr_buffer = []
            
            def read_stream(stream, buffer_list):
                """Read from a stream and buffer output."""
                try:
                    for line in stream:
                        if line:
                            buffer_list.append(line)
                except:
                    pass

            # Start threads to read from both streams
            import time
            from threading import Thread
            
            stdout_thread = Thread(target=read_stream, args=(process.stdout, output_buffer), daemon=True)
            stderr_thread = Thread(target=read_stream, args=(process.stderr, stderr_buffer), daemon=True)
            
            stdout_thread.start()
            stderr_thread.start()
            
            # Parse progress while process is running
            duration = None
            duration_regex = re.compile(r"Duration: (\d+):(\d+):(\d+\.\d+)")
            progress_regex = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
            
            while True:
                # Check if process is still running
                if process.poll() is not None:
                    # Process has ended
                    stdout_thread.join(timeout=1)
                    stderr_thread.join(timeout=1)
                    break
                
                # Update progress from accumulated output
                output = "".join(output_buffer)
                
                if duration is None:
                    match = duration_regex.search(output)
                    if match:
                        hours = int(match.group(1))
                        minutes = int(match.group(2))
                        seconds = float(match.group(3))
                        duration = hours * 3600 + minutes * 60 + seconds
                
                if self.active_download and duration:
                    match = progress_regex.search(output)
                    if match:
                        try:
                            h = int(match.group(1))
                            m = int(match.group(2))
                            s = float(match.group(3))
                            current_time = h * 3600 + m * 60 + s
                            percent = (current_time / duration) * 100.0
                            percent = min(100.0, max(0.0, percent))
                            self.active_download["progress"] = percent
                        except (ValueError, ZeroDivisionError):
                            pass
                
                time.sleep(0.1)

            rc = process.poll()
            output = "".join(output_buffer)
            stderr_output = "".join(stderr_buffer)

            if rc != 0:
                # MSG: Message shown when transcoding fails
                k.log_and_send(_("Error transcoding file: ") + displayed_title, "danger")
                logging.error(f"FFmpeg error code {rc}")
                logging.error(f"Input file: {file_path}")
                logging.error(f"Output file: {output_path}")
                logging.error(f"ffmpeg stdout: {output}")
                logging.error(f"ffmpeg stderr: {stderr_output}")
                
                # Clean up partial output file if it exists
                try:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                        logging.info(f"Cleaned up partial output file: {output_path}")
                except Exception as e:
                    logging.warning(f"Could not delete partial output file {output_path}: {e}")
                
                self.download_errors.append(
                    {
                        "id": str(uuid.uuid4()),
                        "title": displayed_title,
                        "file": file_path,
                        "user": user,
                        "error": stderr_output or output or "Unknown error",
                    }
                )
            else:
                if self.active_download:
                    self.active_download["progress"] = 100
                    self.active_download["status"] = "complete"

                if enqueue:
                    # MSG: Message shown after transcoding is completed and queued
                    k.log_and_send(_("Transcoded and queued: %s") % displayed_title, "success")
                else:
                    # MSG: Message shown after transcoding is completed but not queued
                    k.log_and_send(_("Transcoded: %s") % displayed_title, "success")

                # Verify the output file was created
                if not os.path.exists(output_path):
                    logging.error(f"Transcoded file not found at {output_path}")
                    k.log_and_send(_("Error: Transcoded file was not created: %s") % displayed_title, "danger")
                    self.download_errors.append(
                        {
                            "id": str(uuid.uuid4()),
                            "title": displayed_title,
                            "file": file_path,
                            "user": user,
                            "error": "Transcoded file not created despite ffmpeg exit code 0",
                        }
                    )
                else:
                    # Verify file has content (at least 1MB for a valid video)
                    file_size = os.path.getsize(output_path)
                    min_size = 1024 * 1024  # 1MB minimum
                    
                    if file_size < min_size:
                        logging.error(f"Transcoded file too small ({file_size} bytes) at {output_path}")
                        k.log_and_send(_("Error: Transcoded file is too small (incomplete): %s") % displayed_title, "danger")
                        try:
                            os.remove(output_path)
                        except:
                            pass
                        self.download_errors.append(
                            {
                                "id": str(uuid.uuid4()),
                                "title": displayed_title,
                                "file": file_path,
                                "user": user,
                                "error": f"Transcoded file incomplete (only {file_size} bytes)",
                            }
                        )
                    else:
                        logging.info(f"Transcoded file created successfully: {output_path} ({file_size} bytes)")
                        # Add the transcoded file to the available songs
                        song_is_valid = k.available_songs.add_if_valid(output_path)

                        if song_is_valid:
                            if enqueue:
                                bypass = False
                                if k.queue_manager._is_admin and k.queue_manager._is_admin():
                                    bypass = True
                                k.queue_manager.enqueue(
                                    output_path,
                                    user,
                                    log_action=False,
                                    bypass_queue_restrictions=bypass,
                                )
                        else:
                            # MSG: Message shown after transcoding is completed but the adding to available songs fails
                            k.log_and_send(_("Error adding transcoded song: %s") % displayed_title, "danger")

            # Clean up the temporary source file
            try:
                os.remove(file_path)
            except Exception as e:
                logging.warning(f"Could not delete temporary file {file_path}: {e}")

        except Exception as e:
            logging.error(f"Error during transcoding: {e}")
            # MSG: Message shown when transcoding encounters an exception
            k.log_and_send(_("Transcoding error: ") + displayed_title, "danger")
            self.download_errors.append(
                {
                    "id": str(uuid.uuid4()),
                    "title": displayed_title,
                    "file": file_path,
                    "user": user,
                    "error": str(e),
                }
            )
            # Clean up temporary file on error
            try:
                os.remove(file_path)
            except Exception:
                pass

        return rc if 'rc' in locals() else 1
