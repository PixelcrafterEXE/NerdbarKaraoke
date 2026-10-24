"""Song tag storage, embedded directly in each song file's own metadata.

Tags are never kept in a separate database or sidecar file — the song's MP4
container is the single source of truth. We store one custom freeform atom
holding a small JSON blob, since MP4/iTunes atoms have no standard "mood" or
"language" fields and we want all four tag categories to round-trip exactly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable

from mutagen.mp4 import MP4, MP4FreeForm, MP4StreamInfoError

# Custom freeform atom: "----:mean:name". The "mean" reverse-domain string is
# arbitrary but should stay stable so previously-written tags remain readable.
_ATOM_KEY = "----:com.nerdbarkaraoke:tags"

CATEGORY_KEYS = ("genre", "mood", "language")
STANDALONE_KEYS = ("duet",)

_EMPTY_TAGS = {"genre": [], "mood": [], "language": [], "duet": False}


def empty_tags() -> dict:
    """Return a fresh default tag set (no genre/mood/language, not a duet)."""
    return {"genre": [], "mood": [], "language": [], "duet": False}


def _normalize(tags: dict) -> dict:
    """Coerce arbitrary input into the canonical tag shape, dropping junk."""
    out = empty_tags()
    for key in CATEGORY_KEYS:
        values = tags.get(key) or []
        if isinstance(values, str):
            values = [values]
        cleaned = []
        for v in values:
            v = str(v).strip()
            if v and v not in cleaned:
                cleaned.append(v)
        out[key] = cleaned
    out["duet"] = bool(tags.get("duet", False))
    return out


def read_tags(song_path: str) -> dict:
    """Read this song's tags from its own file metadata.

    Returns the default empty tag set if the file has no tags yet, isn't a
    readable MP4, or the stored atom is corrupt — reading tags should never
    raise for a bad/missing atom.
    """
    try:
        audio = MP4(song_path)
    except (MP4StreamInfoError, OSError) as e:
        logging.warning(f"Could not open '{song_path}' to read tags: {e}")
        return empty_tags()

    raw = audio.tags.get(_ATOM_KEY) if audio.tags else None
    if not raw:
        return empty_tags()

    try:
        data = json.loads(bytes(raw[0]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, IndexError) as e:
        logging.warning(f"Corrupt tag data in '{song_path}': {e}")
        return empty_tags()

    if not isinstance(data, dict):
        return empty_tags()
    return _normalize(data)


def write_tags(song_path: str, tags: dict) -> bool:
    """Write this song's tags into its own file metadata. Returns success."""
    normalized = _normalize(tags)
    try:
        audio = MP4(song_path)
        payload = json.dumps(normalized).encode("utf-8")
        audio[_ATOM_KEY] = [MP4FreeForm(payload)]
        audio.save()
    except (MP4StreamInfoError, OSError) as e:
        logging.warning(f"Failed to write tags to '{song_path}': {e}")
        return False
    return True


def scan_library_vocab(song_paths: Iterable[str]) -> dict:
    """Build the existing-tag vocabulary across the whole library.

    Used both as LLM context (so it prefers reusing an existing tag over
    inventing a near-duplicate) and (unfiltered) to populate autocomplete in the UI.

    Always includes every tag regardless of how rarely it's used — the LLM
    should still know a rare tag exists so it can reuse it instead of minting
    a near-duplicate. Use vocab_above_min_count() for anywhere that should
    hide long-tail tags.
    """
    counts = scan_library_vocab_counts(song_paths)
    return {key: sorted(counts[key].keys(), key=str.lower) for key in CATEGORY_KEYS}


def scan_library_vocab_counts(song_paths: Iterable[str]) -> dict:
    """Build {"genre": {tag: count}, "mood": {...}, "language": {...}} across the library.

    The count is how many songs currently carry that tag — used both to decide
    which tags are common enough to surface in filter/autocomplete UI, and to
    show an admin how big a blast radius removing/renaming a tag would have.
    """
    counts = {key: {} for key in CATEGORY_KEYS}
    for path in song_paths:
        tags = read_tags(path)
        for key in CATEGORY_KEYS:
            for value in tags.get(key) or []:
                counts[key][value] = counts[key].get(value, 0) + 1
    return counts


def vocab_above_min_count(song_paths: Iterable[str], min_count: int) -> dict:
    """Like scan_library_vocab(), but hides tags used by fewer than min_count songs.

    Intended for user-facing filter/autocomplete lists, so one-off or junk tags
    don't clutter them. The underlying song metadata is untouched either way.
    """
    counts = scan_library_vocab_counts(song_paths)
    return {
        key: sorted((t for t, c in counts[key].items() if c >= min_count), key=str.lower)
        for key in CATEGORY_KEYS
    }


def remove_tag_value(song_paths: Iterable[str], category: str, value: str) -> int:
    """Remove a tag value from every song in the library that has it.

    Returns the number of songs updated.
    """
    updated = 0
    for path in song_paths:
        tags = read_tags(path)
        values = tags.get(category) or []
        if value not in values:
            continue
        tags[category] = [v for v in values if v != value]
        if write_tags(path, tags):
            updated += 1
    return updated


def rename_tag_value(song_paths: Iterable[str], category: str, old_value: str, new_value: str) -> int:
    """Rename/merge a tag value across every song in the library that has it.

    If the song already has new_value too, old_value is just dropped (no
    duplicate). Returns the number of songs updated.
    """
    updated = 0
    for path in song_paths:
        tags = read_tags(path)
        values = tags.get(category) or []
        if old_value not in values:
            continue
        new_values = [v for v in values if v != old_value]
        if new_value not in new_values:
            new_values.append(new_value)
        tags[category] = new_values
        if write_tags(path, tags):
            updated += 1
    return updated
