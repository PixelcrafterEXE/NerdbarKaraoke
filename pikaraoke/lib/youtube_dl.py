import json
import logging
import shlex
import socket
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlparse
import re

from pikaraoke.lib.get_platform import get_installed_js_runtime

# yt-dlp command, gets the yt-dlp module from the current python environment
yt_dlp_cmd = [sys.executable, "-m", "yt_dlp"]

# Maximum number of retries for DNS-related failures
_DNS_RETRY_COUNT = 3
_DNS_RETRY_DELAY = 2  # seconds between retries


def _flush_dns_cache() -> None:
    """Flush Python's internal DNS cache and force fresh lookups.

    In long-running containers, cached DNS entries can become stale and
    cause resolution failures. This clears socket's internal getaddrinfo
    cache (if available) to force fresh DNS queries.
    """
    # Clear Python's cached DNS info (not all implementations have this)
    try:
        socket.getaddrinfo.cache_clear()  # type: ignore[attr-defined]
    except AttributeError:
        pass

    # Verify connectivity by resolving youtube.com
    try:
        socket.getaddrinfo("www.youtube.com", 443, proto=socket.IPPROTO_TCP)
        logging.debug("DNS flush: www.youtube.com resolved successfully")
    except socket.gaierror as e:
        logging.warning(f"DNS flush: www.youtube.com resolution still failing: {e}")


def _is_dns_error(error_output: str) -> bool:
    """Check if an error is DNS-related.

    Args:
        error_output: stderr/stdout text from a failed subprocess.

    Returns:
        True if the error appears to be DNS resolution related.
    """
    dns_indicators = (
        "Failed to resolve",
        "Name or service not known",
        "Temporary failure in name resolution",
        "Try again",
        "getaddrinfo",
        "NXDOMAIN",
        "Errno -3",
        "Errno -2",
        "Errno 11001",  # Windows DNS failure
    )
    return any(indicator in error_output for indicator in dns_indicators)


def get_youtubedl_version() -> str:
    """Get the installed yt-dlp version.

    Args:
    Returns:
        Version string of the installed yt-dlp or an error message.
    """
    try:
        cmd = yt_dlp_cmd + ["--version"]
        return subprocess.check_output(cmd).strip().decode("utf8")
    except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as e:
        logging.warning(f"Could not get yt-dlp version: {e}")
        return "Not found"
    except Exception as e:
        logging.error(f"Unexpected error getting yt-dlp version: {e}")
        return "Error"


def get_youtube_id_from_url(url: str) -> str | None:
    """Extract the YouTube video ID from a URL.

    Supports youtube.com/watch?v=, m.youtube.com/?v=, youtu.be/, shorts, and embed formats.

    Args:
        url: YouTube video URL.

    Returns:
        The video ID string, or None if parsing failed.
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        logging.error(f"Error parsing youtube url: {url} ({e})")
        return None

    host = (parsed.netloc or "").lower()
    path = parsed.path or ""
    query = parse_qs(parsed.query)

    # Standard watch URLs: https://www.youtube.com/watch?v=ID
    if "v" in query and query["v"]:
        return query["v"][0]

    # Short links: https://youtu.be/ID
    if "youtu.be" in host:
        path_id = path.strip("/").split("/")[0]
        if path_id:
            return path_id

    # Shorts / embed / legacy formats
    for prefix in ("/shorts/", "/embed/", "/v/"):
        if path.startswith(prefix):
            path_id = path[len(prefix) :].split("/")[0]
            if path_id:
                return path_id

    # Fallback: handle watch?v= with extra params (e.g., &pp=, &list=)
    if "watch?v=" in url:
        id_part = url.split("watch?v=", 1)[1]
        id_part = id_part.split("&", 1)[0]
        id_part = id_part.split("?", 1)[0]
        if id_part:
            return id_part

    logging.error("Error parsing youtube id from url: " + url)
    return None


def upgrade_youtubedl() -> str:
    """Upgrade yt-dlp to the latest version.

    Attempts self-upgrade first, then falls back to pip if needed.

    Args:
    Returns:
        The new version string after upgrade.
    """
    try:
        output = (
            subprocess.check_output(yt_dlp_cmd + ["-U"], stderr=subprocess.STDOUT)
            .decode("utf8")
            .strip()
        )
    except subprocess.CalledProcessError as e:
        output = e.output.decode("utf8")
    except (FileNotFoundError, PermissionError) as e:
        logging.warning(f"Could not run yt-dlp for upgrade: {e}")
        return get_youtubedl_version()

    # Check if already up to date
    if "is up to date" in output.lower():
        logging.debug("yt-dlp is already up to date")
        return get_youtubedl_version()

    upgrade_success = False
    if "pip" in output.lower():
        if not upgrade_success:
            pip_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]

            # Outside a venv, pip requires --break-system-packages on modern Python
            if sys.prefix == sys.base_prefix:
                pip_cmd.append("--break-system-packages")

            try:
                logging.info(f"yt-dlp is outdated! Attempting upgrade via {pip_cmd}...")
                subprocess.check_output(pip_cmd, stderr=subprocess.STDOUT)
                upgrade_success = True
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                logging.error(f"Failed to upgrade yt-dlp using pip: {e}")

    youtubedl_version = get_youtubedl_version()
    if upgrade_success:
        logging.info("Done. Installed version: %s" % youtubedl_version)
    else:
        logging.error("Failed to upgrade yt-dlp.")
    return youtubedl_version


def build_ytdl_download_command(
    video_url: str,
    download_path: str,
    high_quality: bool = False,
    youtubedl_proxy: str | None = None,
    additional_args: str | None = None,
) -> list[str]:
    """Build the yt-dlp command line for downloading a video.

    Args:
        video_url: URL of the video to download.
        download_path: Directory path where videos will be saved.
        high_quality: If True, download up to 1080p; otherwise download mp4.
        youtubedl_proxy: Optional proxy server URL.
        additional_args: Optional additional command-line arguments as a string.

    Returns:
        List of command-line arguments for subprocess execution.
    """
    dl_path = download_path + "%(title)s---%(id)s.%(ext)s"
    file_quality = (
        "bestvideo[ext!=webm][height<=1080]+bestaudio[ext!=webm]/best[ext!=webm]"
        if high_quality
        else "mp4"
    )
    args = [
        "-f",
        file_quality,
        "-o",
        dl_path,
        "-S",
        "vcodec:h264",
        "--newline",
        "--progress-template",
        "download:%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s|%(progress.percent)s",
        "--compat-options",
        "filename-sanitization",
    ]
    cmd = yt_dlp_cmd + args
    preferred_js_runtime = get_installed_js_runtime()
    if preferred_js_runtime and preferred_js_runtime != "deno":
        # Deno is automatically assumed by yt-dlp, and does not need specification here
        cmd += ["--js-runtimes", preferred_js_runtime]
    if youtubedl_proxy:
        cmd += ["--proxy", youtubedl_proxy]
    if additional_args:
        cmd += shlex.split(additional_args)
    cmd += [video_url]
    return cmd


def _sanitize_search_query(text: str) -> str:
    """Sanitize a user-provided search query for use with yt-dlp.

    This removes characters that could affect yt-dlp's argument parsing
    while preserving typical search text.
    """
    # Remove NUL bytes
    text = text.replace("\x00", "")
    # Allow only a conservative set of characters: letters, digits, whitespace,
    # and common punctuation used in search queries.
    text = re.sub(r"[^A-Za-z0-9\s\-\_\.\,\!\?\(\)'/]", "", text)
    # Normalize whitespace
    text = " ".join(text.split())
    return text


def get_search_results(textToSearch: str) -> list[list[str]]:
    """Search YouTube for videos matching the query.

    Includes retry logic for DNS resolution failures that can occur
    in long-running Docker containers (see issue #7).

    Args:
        textToSearch: Search query string.

    Returns:
        List of [title, url, video_id] for each result.

    Raises:
        Exception: If the search fails after all retries.
    """
    logging.info("Searching YouTube for: " + textToSearch)
    num_results = 10
    # Sanitize the search query to avoid unintended yt-dlp argument/option injection.
    sanitized_search = _sanitize_search_query(textToSearch)
    yt_search = 'ytsearch%d:"%s"' % (num_results, sanitized_search)
    cmd = yt_dlp_cmd + ["-j", "--no-playlist", "--flat-playlist", yt_search]
    logging.debug("Youtube-dl search command: " + " ".join(cmd))

    last_error = None
    for attempt in range(_DNS_RETRY_COUNT):
        try:
            output = subprocess.check_output(
                cmd, stderr=subprocess.PIPE
            ).decode("utf-8", "ignore")
            logging.debug("Search results: " + output)
            rc = []
            for each in output.split("\n"):
                if len(each) > 2:
                    j = json.loads(each)
                    if (not "title" in j) or (not "url" in j):
                        continue
                    rc.append([j["title"], j["url"], j["id"]])
            return rc
        except subprocess.CalledProcessError as e:
            stderr_text = e.stderr.decode("utf-8", "ignore") if e.stderr else ""
            stdout_text = e.output.decode("utf-8", "ignore") if e.output else ""
            error_text = stderr_text + stdout_text

            if _is_dns_error(error_text) and attempt < _DNS_RETRY_COUNT - 1:
                logging.warning(
                    f"DNS resolution failed (attempt {attempt + 1}/{_DNS_RETRY_COUNT}), "
                    f"flushing DNS cache and retrying in {_DNS_RETRY_DELAY}s..."
                )
                _flush_dns_cache()
                time.sleep(_DNS_RETRY_DELAY)
                last_error = e
                continue
            logging.debug("Error while executing search: " + str(e))
            raise e
        except Exception as e:
            logging.debug("Error while executing search: " + str(e))
            raise e

    # All retries exhausted
    if last_error:
        raise last_error
    raise Exception("Search failed after all retries")
