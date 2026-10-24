"""LLM-based karaoke song title normalizer.

Uses a locally running Ollama instance (the same one OpenWebUI talks to) to turn
messy YouTube video titles into clean "Title - Artist" karaoke library entries,
matching the house naming convention used throughout the song library.
"""

from __future__ import annotations

import logging
import re

import requests

from pikaraoke.lib.ollama_client import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_TIMEOUT,
    call_ollama,
)

_SYSTEM_PROMPT = """You rename messy YouTube karaoke video titles into clean karaoke library entries.

Output format (ONLY this, nothing else, exactly one line): Title - Artist

Rules:
- Strip channel/branding noise: words like "Karaoke", "Karaoke Version", "Lyrics",
  "Instrumental", "KaraFun", "Zoom Karaoke", "HD", "HQ", "Official Video", "with Lyrics
  On Screen", emoji, bracketed catalog codes like [AVD-119], "No Guide Melody", channel
  taglines, etc.
- Keep only the real song title and the real performing artist/band.
- If the input is written as "Artist - Song", swap it to "Song - Artist".
- Bands/groups: use the group name as the artist, e.g. "Dancing Queen - ABBA".
- Duets/collaborations: separate multiple performers with ", " (comma), e.g.
  "Shallow - Lady Gaga, Bradley Cooper" — unless the performers share one official
  duo/group name that itself contains "&" (e.g. "Simon & Garfunkel", "Shaggy & Rayvon"),
  in which case keep that "&" as part of the artist name.
- "feat."/"ft." credits: keep them only when they are part of the actual recording
  credit (e.g. "Enemy - Imagine Dragons feat. JID"), drop them if they are clearly just
  video-description noise.
- Movies/musicals: use the movie or musical name as the artist slot. Drop qualifiers
  like "(2019 film)" or "Karaoke Version" unless truly needed to disambiguate, e.g.
  "Stars - Les Misérables", "Defying Gravity - Wicked". Keep a "the Musical" suffix
  only when that's how the show is normally referred to (e.g. "Heathers the Musical"),
  otherwise just use the show's name.
- Preserve stylistic artist capitalization as commonly written (e.g. "twenty one
  pilots", "gayle", "fun.", "die ärzte").
- Preserve apostrophes, accents and punctuation that are genuinely part of the title
  or artist name.
- Never include the word "Karaoke" (or translations of it) in your output.
- If you cannot confidently determine BOTH a real title and a real artist, output
  exactly: UNKNOWN
- Output nothing else: no quotes, no explanations, no markdown, just the one line.

Examples:
Input: Bohemian Rhapsody - Queen _ Karaoke Version _ KaraFun
Output: Bohemian Rhapsody - Queen

Input: 'Stars' from Les Misérables - Karaoke Track with Lyrics on Screen
Output: Stars - Les Misérables

Input: ABBA - Dancing Queen (Karaoke Version)
Output: Dancing Queen - ABBA

Input: AnnenMayKantereit • Tommi • Karaoke
Output: Tommi - AnnenMayKantereit

Input: Billy Joel - Vienna (Karaoke Version)
Output: Vienna - Billy Joel

Input: Be Prepared - The Lion King (1994 film) _ Karaoke Version _ KaraFun
Output: Be Prepared - The Lion King

Input: Girl Like Me - Black Eyed Peas ft. Shakira (KARAOKE VERSION)
Output: Girl Like Me - Black Eyed Peas ft. Shakira

Input: Sister Act The Musical - The Life I Never Led - Karaoke Version from Zoom Karaoke
Output: The Life I Never Led - Sister Act The Musical

Input: Peter Fox - Schwarz zu Blau - Karaoke
Output: Schwarz zu Blau - Peter Fox

Input: asdkjalksjd random noise video title with no song info
Output: UNKNOWN
"""

# Words/markers that must never survive into a cleaned suggestion. If the model
# forgets to strip one of these, we reject the suggestion rather than ship junk.
_FORBIDDEN_WORDS = re.compile(
    r"\b(karaoke|karaok[eê]|lyrics?|instrumental|karafun|official\s*video|\bhd\b|\bhq\b)\b",
    re.IGNORECASE,
)

# Matches a contraction apostrophe that got split off from its letter by a
# stray space, e.g. "I' m", "Can' T", "You' ve" — an occasional qwen artifact.
_BROKEN_CONTRACTION = re.compile(r"\w'\s+[A-Za-z]{1,3}(?:\s|$)")


def _validate_and_clean(raw_response: str) -> str | None:
    """Validate and normalize a raw LLM response into a safe "Title - Artist" string.

    Returns None if the response doesn't meet the required shape, contains leftover
    branding noise, or is the model's explicit "I don't know" signal.
    """
    if not raw_response:
        return None

    line = raw_response.strip().splitlines()[0]
    line = line.strip(" \t\"'`“”*")
    line = re.sub(r"\s+", " ", line).strip()

    if not line or line.upper() == "UNKNOWN":
        return None

    # Filesystem safety: reject anything that could escape the filename component.
    if "/" in line or "\\" in line or "\x00" in line:
        return None

    if " - " not in line:
        return None

    title, _, artist = line.partition(" - ")
    title, artist = title.strip(), artist.strip()
    if not title or not artist:
        return None

    if len(line) > 140:
        return None

    if _FORBIDDEN_WORDS.search(line):
        return None

    # qwen occasionally mangles contractions ("I' m", "Can' T") or emits
    # unbalanced parens. Reject rather than ship a visibly broken filename —
    # keeping the original name is always the safer fallback.
    if _BROKEN_CONTRACTION.search(line):
        return None
    if line.count("(") != line.count(")"):
        return None

    return line


def suggest_title(
    raw_title: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
) -> str | None:
    """Ask the local Ollama LLM for a clean "Title - Artist" suggestion.

    Args:
        raw_title: The messy source title (e.g. a filename stem or video title).
        model: Ollama model name to use (defaults to DEFAULT_OLLAMA_MODEL).
        base_url: Ollama server base URL (defaults to DEFAULT_OLLAMA_URL).
        timeout: Request timeout in seconds (defaults to DEFAULT_TIMEOUT).

    Returns:
        A cleaned "Title - Artist" string (which may be identical to raw_title if
        the LLM considers it already well-formatted), or None if the LLM is
        unreachable, times out, or can't confidently produce a valid suggestion.
    """
    raw_title = (raw_title or "").strip()
    if not raw_title:
        return None

    model = model or DEFAULT_OLLAMA_MODEL
    base_url = base_url or DEFAULT_OLLAMA_URL
    timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

    prompt = f"{_SYSTEM_PROMPT}\nInput: {raw_title}\nOutput:"

    try:
        raw_response = call_ollama(prompt, model=model, base_url=base_url, timeout=timeout)
    except requests.RequestException as e:
        logging.warning(f"LLM rename request failed for '{raw_title}': {e}")
        return None
    except (ValueError, KeyError) as e:
        logging.warning(f"LLM rename response was malformed for '{raw_title}': {e}")
        return None

    cleaned = _validate_and_clean(raw_response)
    if cleaned is None:
        logging.info(f"LLM rename suggestion rejected for '{raw_title}': {raw_response!r}")
        return None

    return cleaned
