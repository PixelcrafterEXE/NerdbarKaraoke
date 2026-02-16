"""
This file contains logic for Auto-tagging and Auto-renaming files.

Here's a list of tags created for each file
    LLM-based tags:
        - genres (pop, rock, grunge, ...)
        - moods (happy, calm, angry, enthusiastic...)
        - themes (breakup, crush, toxicity, ...)
        - if it's a soundtrack (game, movie, tv-show)
        - languages (german, english, ...)
        - singers (male-singer, female-singer, duet, choir)
        - vocal range (high, medium low)
    since these tags are more usefull if instanced often, a list of existing tags is passed to the LLM
        
    API-based tags from https://api.getsong.co/:
        - Tempo in BPM
        - Key

    API-based tags from last.fm:
        - release dates

API Keys are stored as environment variables:
    - GETSONGKEY_API_KEY
    - LASTFM_API_KEY
    - GEMINI_API_KEY
All tags are stored in the mp4 file itself.

Autorenaming originaly was implemented algorithmicaly to rename files to a more consistent format, but since the LLM can 
also be used to extract the artist and title more consitently it will be used instead. 
The format is "title - artist.mp4".

    If there is an individual artist, the artist name is used, otherwise the group name or film name is used.
    If the LLM cannot extract a title, the original filename is used as the title.

"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests


logger = logging.getLogger(__name__)

# External endpoints (kept as constants to make testing / future updates easy)
GETSONG_BASE = "https://api.getsong.co"
LASTFM_BASE = "http://ws.audioscrobbler.com/2.0/"
DEFAULT_TIMEOUT = 5  # seconds


class TaggingAPIError(Exception):
    """Raised for non-fatal tagging API failures (kept lightweight)."""


def _first_of(source: Dict[str, Any], *candidates: str) -> Optional[Any]:
    """Return the first non-empty value from `source` for the given keys."""
    for k in candidates:
        if not isinstance(source, dict):
            break
        v = source.get(k)
        if v is not None:
            return v
    return None


def _parse_year_from_string(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = re.search(r"(19|20)\d{2}", s)
    return int(m.group(0)) if m else None


def parse_filename_for_artist_title(path_or_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Try to extract (title, artist) from a filename.

    Expected canonical filename in this project is "title - artist.ext"; we
    try that first and otherwise return the stem as the title and None for the
    artist.
    """
    stem = Path(path_or_name).stem
    if " - " in stem:
        parts = stem.split(" - ", 1)
        title = parts[0].strip() or None
        artist = parts[1].strip() or None
        return title, artist
    return stem or None, None


def fetch_getsong_features(
    artist: str,
    title: str,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Query the GetSong (getsongbpm) API for tempo/key metadata.

    Implementation follows the documented flow:
      1. Call `/search/?type=song&lookup=...` to find candidate songs.
      2. If a candidate with an `id` is found, call `/song/?id=<id>` to get
         authoritative `tempo` / `key_of` values.

    Authentication: `X-API-KEY` header is preferred; falls back to
    `api_key` query parameter if necessary.

    Returns a dict containing any of: ``bpm``, ``key`` and a ``raw`` blob
    with the API responses. On API errors the `raw` value will include the
    HTTP status and body for inspection.
    """
    api_key = os.environ.get("GETSONGKEY_API_KEY")
    if not api_key:
        logger.debug("GETSONGKEY_API_KEY not set — skipping Getsong lookup")
        return {}

    session = session or requests.Session()

    # 1) search
    search_url = f"{GETSONG_BASE.rstrip('/')}/search/"
    lookup = title if not artist else f"{title} {artist}"
    search_params = {"type": "song", "lookup": lookup, "limit": 5}
    headers = {"X-API-KEY": api_key, "Accept": "application/json"}

    def _do_request(url: str, params: Dict[str, Any], use_header: bool = True) -> Tuple[int, Any]:
        """Perform GET; return (status, parsed_json_or_text)."""
        try:
            if use_header:
                resp = session.get(url, params=params, headers=headers, timeout=timeout)
            else:
                resp = session.get(url, params=dict(params, api_key=api_key), timeout=timeout)
        except Exception as exc:
            logger.debug("HTTP request to %s failed: %s", url, exc)
            return 0, str(exc)
        status = getattr(resp, "status_code", 0)
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        return status, body

    status, payload = _do_request(search_url, search_params, use_header=True)
    if status != 200:
        # try fallback with api_key as query param
        status2, payload2 = _do_request(search_url, search_params, use_header=False)
        # include diagnostic info
        logger.debug("Getsong search header-status=%s fallback-status=%s", status, status2)
        return {"raw": {"search": {"status": status or None, "body": payload}, "search_fallback": {"status": status2 or None, "body": payload2}}}

    # payload should be a dict with 'search' key
    results = payload.get("search") if isinstance(payload, dict) else payload
    if not results:
        return {"raw": {"search": payload}}

    # choose best match (prefer exact artist match)
    chosen = None
    for item in results:
        if not isinstance(item, dict):
            continue
        item_artist = None
        a = item.get("artist")
        if isinstance(a, dict):
            item_artist = a.get("name")
        elif isinstance(a, list) and a:
            first = a[0]
            item_artist = first.get("name") if isinstance(first, dict) else first
        elif isinstance(a, str):
            item_artist = a
        if artist and item_artist and item_artist.lower() == artist.lower():
            chosen = item
            break
    if not chosen:
        chosen = results[0]

    # if we have an ID, fetch the detailed /song/ endpoint
    song_id = chosen.get("id") if isinstance(chosen, dict) else None
    if not song_id:
        # no id -> return whatever we can extract from the search item
        bpm = _first_of(chosen, "tempo", "bpm")
        key = _first_of(chosen, "key_of", "open_key", "key")
        return {"bpm": bpm, "key": key, "raw": {"search": payload}}

    # 2) get full song details
    song_url = f"{GETSONG_BASE.rstrip('/')}/song/"
    song_params = {"id": song_id}
    status_s, payload_s = _do_request(song_url, song_params, use_header=True)
    if status_s != 200:
        status_s2, payload_s2 = _do_request(song_url, song_params, use_header=False)
        logger.debug("Getsong /song/ header-status=%s fallback-status=%s", status_s, status_s2)
        return {"raw": {"search": payload, "song_error": {"status": status_s or None, "body": payload_s}, "song_error_fallback": {"status": status_s2 or None, "body": payload_s2}}}

    # payload_s expected to be {"song": { ... }}
    song_obj = payload_s.get("song") if isinstance(payload_s, dict) else None
    if not song_obj:
        return {"raw": {"search": payload, "song": payload_s}}

    bpm = _first_of(song_obj, "tempo", "bpm", "tempo")
    key = _first_of(song_obj, "key_of", "open_key", "key")

    return {"bpm": bpm, "key": key, "raw": {"search": payload, "song": payload_s}}



def fetch_lastfm_release_date(
    artist: str,
    title: str,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Query Last.fm for release / published date information.

    Uses the `track.getInfo` endpoint and extracts either the album
    `releasedate` or the track `wiki.published` field where available.
    """
    api_key = os.environ.get("LASTFM_API_KEY")
    if not api_key:
        logger.debug("LASTFM_API_KEY not set — skipping Last.fm lookup")
        return {}

    session = session or requests.Session()
    params = {
        "method": "track.getInfo",
        "api_key": api_key,
        "artist": artist,
        "track": title,
        "format": "json",
    }

    try:
        resp = session.get(LASTFM_BASE, params=params, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()

        track = payload.get("track", {}) if isinstance(payload, dict) else {}
        album = track.get("album", {}) if isinstance(track, dict) else {}

        release_date_str = None
        if album and isinstance(album, dict):
            # Last.fm sometimes provides a human readable releasedate string
            release_date_str = album.get("releasedate")

        if not release_date_str and isinstance(track, dict):
            wiki = track.get("wiki")
            if isinstance(wiki, dict):
                release_date_str = wiki.get("published")

        year = _parse_year_from_string(release_date_str)

        return {"release_date": (release_date_str or None), "year": year, "raw": payload}

    except Exception as exc:
        logger.debug("Last.fm lookup failed for %s - %s: %s", artist, title, exc)
        return {}


def get_tag_data(
    file_path: str,
    artist: Optional[str] = None,
    title: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """Return combined tag data for a song file.

    The function will:
    - try to infer ``title`` and ``artist`` from the filename when not
      provided (expects the project's canonical "title - artist.ext" style),
    - query Getsong for ``bpm``/``key`` and Last.fm for release date/year,
    - return a single normalized dictionary with `tags` and `raw` API blobs.

    The function never raises on external API failures — missing values will
    simply be omitted from the returned `tags` dict.
    """
    session = session or requests.Session()

    parsed_title, parsed_artist = parse_filename_for_artist_title(file_path)
    title = title or parsed_title
    artist = artist or parsed_artist

    result: Dict[str, Any] = {"artist": artist, "title": title, "tags": {}, "raw": {}}

    if not (artist and title):
        logger.debug("No artist/title available for '%s' — API lookups skipped", file_path)
        return result

    gs = fetch_getsong_features(artist, title, session=session)
    lf = fetch_lastfm_release_date(artist, title, session=session)

    # merge useful fields into `tags`
    if gs:
        if gs.get("bpm"):
            result["tags"]["bpm"] = gs.get("bpm")
        if gs.get("key"):
            result["tags"]["key"] = gs.get("key")
        result["raw"]["getsong"] = gs.get("raw")

    if lf:
        if lf.get("release_date"):
            result["tags"]["release_date"] = lf.get("release_date")
        if lf.get("year"):
            result["tags"]["year"] = lf.get("year")
        result["raw"]["lastfm"] = lf.get("raw")

    return result


__all__ = [
    "fetch_getsong_features",
    "fetch_lastfm_release_date",
    "get_tag_data",
    "parse_filename_for_artist_title",
]

