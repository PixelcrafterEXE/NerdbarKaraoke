"""FFmpeg utilities for media processing and transcoding."""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import re
import subprocess
from typing import TYPE_CHECKING, Any

import ffmpeg

from pikaraoke.lib.get_platform import is_running_in_docker

if TYPE_CHECKING:
    from pikaraoke.lib.file_resolver import FileResolver


def get_media_duration(file_path: str) -> int | None:
    """Get the duration of a media file in seconds.

    Args:
        file_path: Path to the media file.

    Returns:
        Duration in seconds (rounded), or None if unable to determine.
    """
    try:
        duration = ffmpeg.probe(file_path)["format"]["duration"]
        return round(float(duration))
    except:
        return None


def build_ffmpeg_cmd(
    fr: FileResolver,
    semitones: int = 0,
    normalize_audio: bool = True,
    force_mp4_encoding: bool = False,
    buffer_fully_before_playback: bool = False,
    avsync: float = 0,
    cdg_pixel_scaling: bool = False,
) -> Any:
    """Build an ffmpeg command for transcoding media.

    Handles video/audio codec selection, pitch shifting, audio normalization,
    and CDG file rendering.

    Args:
        fr: FileResolver instance with source file information.
        semitones: Number of semitones to shift pitch (0 = no shift).
        normalize_audio: Whether to apply loudness normalization.
        force_mp4_encoding: If True, force mp4 encoding.
        avsync: Audio/video sync adjustment in seconds.
        cdg_pixel_scaling: Enable pixel scaling for CDG rendering.

    Returns:
        ffmpeg stream object ready to execute with run_async().
    """
    avsync = float(avsync)
    is_cdg = fr.cdg_file_path is not None
    is_transposed = semitones != 0

    if fr.file_path is None:
        raise ValueError("File path is required to build ffmpeg command")

    # Use h/w acceleration on Pi
    using_hardware_encoder = supports_hardware_h264_encoding()
    default_vcodec = "h264_v4l2m2m" if using_hardware_encoder else "libx264"

    # CDG always needs encoding; MP4 can copy video stream (already H.264 compatible)
    # WEBM uses VP8/VP9 which must be transcoded to H.264 for fMP4 containers
    if is_cdg:
        vcodec = "libx264"
    else:
        vcodec = "copy" if fr.file_extension == ".mp4" else default_vcodec

    # Optimize bitrate: CDG is simple graphics (500k), video files need more
    # Pi 3B+ struggles with 5M in real-time, 2M provides better stability
    if is_cdg:
        vbitrate = "500k"
    elif using_hardware_encoder:
        vbitrate = "2M"
    else:
        vbitrate = "5M"

    # Copy audio if no processing needed, otherwise re-encode with AAC
    # CDG always re-encodes audio for compatibility
    acodec = "aac" if is_cdg or is_transposed or normalize_audio or avsync != 0 else "copy"

    # For container formats with VFR or timestamp issues, use genpts
    if fr.file_extension in [".webm", ".avi", ".mov", ".mkv"]:
        input = ffmpeg.input(fr.file_path, **{"fflags": "+genpts"})
    else:
        input = ffmpeg.input(fr.file_path)
    audio = input.audio

    # Audio sync adjustment: delay or trim
    if avsync > 0:
        audio = audio.filter("adelay", f"{avsync * 1000}|{avsync * 1000}")
    elif avsync < 0:
        audio = audio.filter("atrim", start=-avsync)

    # Pitch shifting: 2^(semitones/12)
    if is_transposed:
        audio = audio.filter("rubberband", pitch=2 ** (semitones / 12))

    # Loudness normalization
    if normalize_audio:
        audio = audio.filter("loudnorm", i=-16, tp=-1.5, lra=11)

    # Video source: CDG input or original video stream
    if is_cdg:
        logging.info("Playing CDG/MP3 file: " + fr.file_path)
        cdg_input = ffmpeg.input(fr.cdg_file_path, copyts=None)
        video = cdg_input.video.filter("fps", fps=25)
        if cdg_pixel_scaling:
            video = video.filter("scale", -1, 720, flags="neighbor")
    else:
        video = input.video

    # Build output based on format
    if force_mp4_encoding:
        movflags = (
            "+faststart" if buffer_fully_before_playback else "frag_keyframe+default_base_moof"
        )
        output = ffmpeg.output(
            audio,
            video,
            fr.output_file,
            vcodec=vcodec,
            acodec=acodec,
            preset="ultrafast",
            listen=1,
            f="mp4",
            video_bitrate=vbitrate,
            movflags=movflags,
            **({"pix_fmt": "yuv420p"} if is_cdg else {}),
        )
    else:
        # HLS format with fMP4 segments
        # Both MP4 and HLS streaming modes use this - difference is in serving:
        # - mp4: Stream concatenates init + segments for progressive playback
        # - hls: Browser requests segments via .m3u8 playlist
        output = ffmpeg.output(
            audio,
            video,
            fr.output_file,
            vcodec=vcodec,
            acodec="aac",
            audio_bitrate="192k",
            ac=2,  # Force stereo
            ar=48000,  # Standard sample rate
            preset="ultrafast",
            f="hls",
            hls_time=3,
            hls_list_size=0,
            hls_playlist_type="event",
            hls_segment_type="fmp4",
            hls_fmp4_init_filename=fr.init_filename,
            hls_segment_filename=fr.segment_pattern,
            video_bitrate=vbitrate,
            # CDG needs pix_fmt for proper color space
            **({"pix_fmt": "yuv420p"} if is_cdg else {}),
            **{
                "vsync": "cfr",
                "avoid_negative_ts": "make_zero",
            },
        )

    args = output.get_args()
    logging.debug(f"COMMAND: ffmpeg " + " ".join(args))
    return output


def get_ffmpeg_version() -> str:
    """Get the installed FFmpeg version string.

    Returns:
        Version string, or an error message if FFmpeg is not installed
        or version cannot be parsed.
    """
    try:
        # Execute the command 'ffmpeg -version'
        result = subprocess.run(
            ["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        # Parse the first line to get the version
        first_line = result.stdout.split("\n")[0]
        version_info = first_line.split(" ")[2]  # Assumes the version info is the third element
        return version_info
    except FileNotFoundError:
        return "FFmpeg is not installed"
    except IndexError:
        return "Unable to parse FFmpeg version"


def is_transpose_enabled() -> bool:
    """Check if FFmpeg has the rubberband filter for pitch shifting.

    Returns:
        True if rubberband filter is available, False otherwise.
    """
    try:
        filters = subprocess.run(["ffmpeg", "-filters"], capture_output=True)
    except FileNotFoundError:
        return False
    except IndexError:
        return False
    return "rubberband" in filters.stdout.decode()


def supports_hardware_h264_encoding() -> bool:
    """Check if hardware H.264 encoding (h264_v4l2m2m) is available.

    Only returns True on ARM architecture (Raspberry Pi) where h264_v4l2m2m
    is actually supported. On x86/Intel systems, returns False to use software encoding.

    Returns:
        True if hardware encoding is available, False otherwise.
    """
    # Check CPU architecture first - h264_v4l2m2m only works on ARM
    arch = platform.machine().lower()
    is_arm = any(arm_variant in arch for arm_variant in ["arm", "aarch"])

    if not is_arm:
        # Not ARM (probably Intel x86/x64), don't use h264_v4l2m2m
        logging.debug(f"CPU architecture {arch} is not ARM, using software encoder")
        return False

    if is_running_in_docker():
        # Docker containers do not have access to the GPU
        logging.debug("Running in Docker where GPU access is not available, using software encoder")
        return False

    # On ARM, check if h264_v4l2m2m is available
    try:
        codecs = subprocess.run(["ffmpeg", "-codecs"], capture_output=True)
    except FileNotFoundError:
        return False
    except IndexError:
        return False

    has_encoder = "h264_v4l2m2m" in codecs.stdout.decode()
    if has_encoder:
        logging.info("ARM platform detected, using h264_v4l2m2m hardware encoder")
    else:
        logging.debug("ARM platform but h264_v4l2m2m not available")

    return has_encoder


def is_ffmpeg_installed() -> bool:
    """Check if FFmpeg is installed and accessible.

    Returns:
        True if FFmpeg is installed, False otherwise.
    """
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
    except FileNotFoundError:
        return False
    return True


def _detect_silence_intervals(
    file_path: str,
    threshold_db: float = -50,
    min_silence_duration: float = 0.5,
) -> list[tuple[float, float | None]]:
    """Detect silence intervals using ffmpeg silencedetect.

    Returns a list of (start, end) pairs. End is None if silence continues to EOF.
    """
    cmd = [
        "ffmpeg",
        "-i",
        file_path,
        "-af",
        f"silencedetect=n={threshold_db}dB:d={min_silence_duration}",
        "-f",
        "null",
        "-",
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    output = result.stderr or ""

    silence_start_re = re.compile(r"silence_start:\s*(?P<start>\d+(?:\.\d+)?)")
    silence_end_re = re.compile(r"silence_end:\s*(?P<end>\d+(?:\.\d+)?)(?:\s*\|\s*silence_duration:\s*(?P<duration>\d+(?:\.\d+)?))?")

    intervals: list[tuple[float, float | None]] = []
    for line in output.splitlines():
        start_match = silence_start_re.search(line)
        if start_match:
            start = float(start_match.group("start"))
            intervals.append((start, None))
            continue

        end_match = silence_end_re.search(line)
        if end_match and intervals:
            end = float(end_match.group("end"))
            last_start, _ = intervals[-1]
            intervals[-1] = (last_start, end)

    return intervals


def trim_silence_in_place(
    file_path: str,
    threshold_db: float = -50,
    min_silence_duration: float = 0.5,
    edge_tolerance: float = 0.1,
) -> bool:
    """Trim leading/trailing silence from a media file in-place.

    Uses ffmpeg silencedetect to find leading/trailing silence, then trims
    with stream copy. Returns True if trimming was applied.
    """
    duration = get_media_duration(file_path)
    if duration is None:
        logging.warning(f"Could not determine media duration for trimming: {file_path}")
        return False

    intervals = _detect_silence_intervals(
        file_path,
        threshold_db=threshold_db,
        min_silence_duration=min_silence_duration,
    )

    if not intervals:
        return False

    start_trim = 0.0
    end_trim = None

    first_start, first_end = intervals[0]
    if first_start <= edge_tolerance and first_end is not None:
        start_trim = first_end

    last_start, last_end = intervals[-1]
    if last_end is None:
        end_trim = last_start
    elif duration - last_end <= edge_tolerance:
        end_trim = last_start

    if end_trim is None and start_trim <= edge_tolerance:
        return False

    if end_trim is None:
        end_trim = float(duration)

    trim_duration = end_trim - start_trim
    if trim_duration <= 0 or trim_duration >= duration:
        return False

    base, ext = os.path.splitext(file_path)
    temp_path = f"{base}.trimmed{ext}"

    cmd = ["ffmpeg", "-y", "-i", file_path]
    if start_trim > edge_tolerance:
        cmd += ["-ss", f"{start_trim}"]
    cmd += ["-t", f"{trim_duration}", "-c", "copy", "-map", "0", "-avoid_negative_ts", "make_zero", temp_path]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        logging.warning(
            "Silence trimming failed for %s: %s",
            file_path,
            (result.stderr or result.stdout).strip(),
        )
        if os.path.exists(temp_path):
            with contextlib.suppress(OSError):
                os.remove(temp_path)
        return False

    os.replace(temp_path, file_path)
    logging.info(
        "Trimmed silence for %s (start=%.2fs, end=%.2fs)",
        file_path,
        start_trim,
        end_trim,
    )
    return True
