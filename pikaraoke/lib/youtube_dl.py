import json
import logging
import shlex
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

from pikaraoke.lib.get_platform import get_installed_js_runtime

# yt-dlp command, gets the yt-dlp module from the current python environment
yt_dlp_cmd = [sys.executable, "-m", "yt_dlp"]


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


def get_search_results(textToSearch: str) -> list[list[str]]:
    """Search YouTube for videos matching the query.

    Args:
        textToSearch: Search query string.

    Returns:
        List of [title, url, video_id] for each result.

    Raises:
        Exception: If the search fails.
    """
    logging.info("Searching YouTube for: " + textToSearch)
    num_results = 10
    yt_search = 'ytsearch%d:"%s"' % (num_results, textToSearch)
    cmd = yt_dlp_cmd + ["-j", "--no-playlist", "--flat-playlist", yt_search]
    logging.debug("Youtube-dl search command: " + " ".join(cmd))
    try:
        output = subprocess.check_output(cmd).decode("utf-8", "ignore")
        logging.debug("Search results: " + output)
        rc = []
        for each in output.split("\n"):
            if len(each) > 2:
                j = json.loads(each)
                if (not "title" in j) or (not "url" in j):
                    continue
                rc.append([j["title"], j["url"], j["id"]])
        return rc
    except Exception as e:
        logging.debug("Error while executing search: " + str(e))
        raise e
