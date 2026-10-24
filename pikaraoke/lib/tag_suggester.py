"""LLM-based tag suggestions: genre, mood, language, and the "duet" flag.

Uses the same local Ollama instance as llm_renamer. The existing tag vocabulary
across the library is always passed as context so the model prefers reusing a
tag that's already in use over minting a near-duplicate (e.g. "Pop Rock" vs a
library that already has "Pop-Rock"), while still being free to introduce a
genuinely new tag when nothing in the vocabulary fits.
"""

from __future__ import annotations

import json
import logging
import re

import requests

from pikaraoke.lib import tag_manager
from pikaraoke.lib.ollama_client import call_ollama

_SYSTEM_PROMPT = """You tag karaoke songs for a bar's song library. Given a song's
"Title - Artist" and the tags already in use across the library, choose tags for
this song.

Categories — these are DIFFERENT DIMENSIONS, never mix them up:
- genre: 1-2 real MUSIC genres from established music taxonomy (e.g. "Pop", "Rock",
  "Musical", "Hip-Hop", "Schlager", "Folk", "Metal", "Country", "Electronic", "Jazz",
  "Punk", "Indie", "Reggae", "R&B"). A genre is a style of music, period.
  - NEVER put a language, country, or nationality name in genre (not "German", not
    "French", not "English", not "Spanish", not "American") — that's what the
    language field is for, and genre must not duplicate it.
  - NEVER put a topic, subject, or theme in genre (not "Science", not "History",
    not "Christmas", not a character or movie name unless the song is literally
    from that musical/show).
  - NEVER copy a word or phrase directly from the song's own title or artist name
    into genre unless that word is ALSO a real, independently-recognized genre
    name (e.g. "Pop" is fine even if it appears in the title; a proper noun from
    the title, like part of the song's own name, is never a genre).
  - "World" music means non-Western traditional/folk styles — do NOT use it as a
    generic label for "this song isn't in English." A German pop song is "Pop",
    not "World" and not "German".
- mood: 1-2 words describing the song's EMOTIONAL feel or energy (e.g. "Upbeat",
  "Melancholic", "Anthemic", "Romantic", "Angry", "Calm", "Dark", "Nostalgic").
  - Mood is strictly about how the song FEELS, never what it's ABOUT. A song that
    happens to be educational, political, comedic-in-subject, or about a specific
    topic still gets a normal emotional mood (e.g. "Upbeat", "Playful") — never a
    topic label like "Educational", "Political", "Scientific", or "Historical".
  - A song's language or country of origin is NOT a mood either (not "Traditional"
    just because it's German, not "Exotic" just because it's foreign).
- Don't stereotype by language. Most German-language pop/rock/hip-hop is just
  "Pop"/"Rock"/"Hip-Hop" like anywhere else — reserve "Schlager" only for music
  you recognize as that specific traditional sentimental style (e.g. Udo Jürgens,
  Helene Fischer), not as a generic bucket for "this is a German song."
- Only include a second language if the song is clearly, deliberately bilingual
  throughout. If you're not confident, list just the one language you're sure of.
- language: the language(s) the song is actually SUNG in (e.g. "English", "German"). Usually just one.
- duet: true only if the song is written to be sung by two (or more) distinct
  vocal parts together (e.g. a male/female duet, a musical theatre duet). A
  song that merely credits a guest rapper or "feat." artist is NOT automatically
  a duet — only mark true when there are genuinely two singing parts meant to
  be performed together.

Rules:
- STRONGLY prefer reusing a tag that already appears in "Existing tags" below
  over inventing a new one, as long as it's a reasonable fit. Match its exact
  spelling/capitalization when you reuse it.
- Only introduce a new tag when nothing existing fits reasonably well, and only
  ever a real genre / real emotional mood as defined above.
- Keep tags short (1-3 words each), in English, Title Case.
- Output STRICT JSON only, nothing else, this exact shape:
  {{"genre": ["..."], "mood": ["..."], "language": ["..."], "duet": true|false}}
  No markdown code fences, no // comments, no explanation before or after — just
  that one JSON object.

Existing tags in this library:
genre: {genre_vocab}
mood: {mood_vocab}
language: {language_vocab}

Examples:
Song: Shallow - Lady Gaga, Bradley Cooper
Existing tags: genre: [Pop, Rock]; mood: [Emotional, Upbeat]; language: [English]
Output: {{"genre": ["Pop", "Rock"], "mood": ["Emotional"], "language": ["English"], "duet": true}}

Song: Stars - Les Misérables
Existing tags: genre: [Musical, Pop]; mood: [Emotional]; language: [English]
Output: {{"genre": ["Musical"], "mood": ["Emotional"], "language": ["English"], "duet": false}}

Song: 99 Luftballons - Nena
Existing tags: genre: [Pop, Rock]; mood: [Upbeat]; language: [English]
Output: {{"genre": ["Pop"], "mood": ["Upbeat"], "language": ["German"], "duet": false}}

Song: Griechischer Wein - Udo Jürgens
Existing tags: genre: [Pop, Schlager]; mood: [Romantic]; language: [German]
Output: {{"genre": ["Schlager"], "mood": ["Romantic"], "language": ["German"], "duet": false}}
(Note: "Griechischer Wein" literally means "Greek Wine" in German — it is the
song's own title, NOT a genre. Never copy title words into genre like that.)

Song: Defining Gravity - A Capella Science
Existing tags: genre: [Pop, Rock]; mood: [Upbeat]; language: [English]
Output: {{"genre": ["A Capella"], "mood": ["Playful"], "language": ["English"], "duet": false}}
(Note: the song explains a science topic, but "Science" is a topic, not a genre,
and "Educational" is a topic, not an emotional mood — so neither is used.)
"""

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_CODE_FENCE = re.compile(r"```(?:json)?")
_LINE_COMMENT = re.compile(r"//[^\n]*")


def _format_vocab(values: list[str]) -> str:
    if not values:
        return "(none yet -- this is one of the first songs tagged, introduce reasonable new tags freely)"
    return "[" + ", ".join(values) + "]"


def _extract_json_dict(raw_response: str) -> dict | None:
    """Pull a JSON object out of a raw LLM response, tolerating the occasional
    markdown code fence or trailing // comment the model adds despite being
    told not to.
    """
    text = _CODE_FENCE.sub("", raw_response or "")
    text = _LINE_COMMENT.sub("", text)

    match = _JSON_BLOCK.search(text)
    if not match:
        return None

    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None

    return data if isinstance(data, dict) else None


def _validate_and_clean(raw_response: str, vocab: dict) -> dict | None:
    """Parse and sanity-check the LLM's JSON, canonicalizing against vocab casing."""
    if not raw_response:
        return None

    data = _extract_json_dict(raw_response)
    if data is None:
        return None

    cleaned = tag_manager.empty_tags()

    for key in tag_manager.CATEGORY_KEYS:
        values = data.get(key)
        if not isinstance(values, list):
            continue
        canon_lookup = {v.lower(): v for v in vocab.get(key, [])}
        result = []
        for v in values:
            if not isinstance(v, str):
                continue
            v = v.strip()
            if not v or len(v) > 40:
                continue
            if not v.isascii():
                # Tags must be plain English per the prompt; drop anything that
                # slipped through in another script (e.g. CJK) instead of
                # shipping a tag nobody in this library can read/filter by.
                continue
            # Prefer the library's existing casing for this tag if it matches;
            # otherwise Title Case a fully-lowercase new tag (e.g. "folk" -> "Folk"),
            # leaving anything with intentional mixed case (e.g. "K.I.Z.") alone.
            if v.lower() in canon_lookup:
                v = canon_lookup[v.lower()]
            elif v.islower():
                v = v.title()
            if v not in result:
                result.append(v)
        cleaned[key] = result[:3]

    cleaned["duet"] = bool(data.get("duet", False))

    return cleaned


def suggest_tags(
    clean_title: str,
    vocab: dict,
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    retries: int = 1,
) -> dict | None:
    """Ask the local Ollama LLM for genre/mood/language/duet tags for a song.

    Args:
        clean_title: The song's clean "Title - Artist" display name.
        vocab: {"genre": [...], "mood": [...], "language": [...]} — the tag
            vocabulary already in use across the library (from
            tag_manager.scan_library_vocab), passed as context.
        retries: Extra attempts if the model fails to produce usable JSON (it
            occasionally misreads the task entirely, e.g. refusing because
            "no existing tags" instead of introducing new ones — a fresh
            attempt almost always recovers).

    Returns:
        A normalized tag dict, or None if every attempt failed — the LLM was
        unreachable, timed out, or never returned something we could parse.
    """
    clean_title = (clean_title or "").strip()
    if not clean_title:
        return None

    prompt = (
        _SYSTEM_PROMPT.format(
            genre_vocab=_format_vocab(vocab.get("genre", [])),
            mood_vocab=_format_vocab(vocab.get("mood", [])),
            language_vocab=_format_vocab(vocab.get("language", [])),
        )
        + f"\nSong: {clean_title}\nOutput:"
    )

    for attempt in range(retries + 1):
        try:
            raw_response = call_ollama(prompt, model=model, base_url=base_url, timeout=timeout)
        except requests.RequestException as e:
            logging.warning(f"Tag suggestion request failed for '{clean_title}' (attempt {attempt + 1}): {e}")
            continue

        cleaned = _validate_and_clean(raw_response, vocab)
        if cleaned is not None:
            return cleaned

        logging.info(f"Tag suggestion rejected for '{clean_title}' (attempt {attempt + 1}): {raw_response!r}")

    return None


_BACKFILL_PROMPT = """You maintain tag consistency in a karaoke song library. A brand-new
"{category}" tag "{value}" was just created for the song "{origin_title}" because nothing
already in the library fit it.

Below is a numbered list of OTHER songs already in the library (not including that one).
Look through them and decide which ones this same tag should also apply to.

Rules:
- Only include a song if you're genuinely confident the tag fits it too.
- It's completely fine to return an empty list if none do — don't force matches.
- Reference songs ONLY by their number from the list below.
- Output STRICT JSON only: a JSON array of integers, e.g. [3, 17, 42] or [].

Songs:
{numbered_songs}

Output:"""

_INT_ARRAY = re.compile(r"\[[\s\d,]*\]")


def backfill_tag_across_library(
    category: str,
    value: str,
    origin_title: str,
    other_songs: dict[str, str],
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
) -> list[str]:
    """Ask the LLM which OTHER songs in the library should also get a brand-new tag.

    Args:
        category: One of tag_manager.CATEGORY_KEYS.
        value: The newly-introduced tag value.
        origin_title: Clean title of the song the tag was first created for.
        other_songs: {song_path: clean_title} for every other song to consider.

    Returns:
        The subset of other_songs' paths judged to also deserve this tag.
    """
    if not other_songs:
        return []

    paths = list(other_songs.keys())
    numbered = "\n".join(f"{i + 1}. {other_songs[p]}" for i, p in enumerate(paths))
    prompt = _BACKFILL_PROMPT.format(
        category=category, value=value, origin_title=origin_title, numbered_songs=numbered
    )

    try:
        raw_response = call_ollama(
            prompt, model=model, base_url=base_url, timeout=timeout or 90, temperature=0.1
        )
    except requests.RequestException as e:
        logging.warning(f"Tag backfill request failed for '{value}' ({category}): {e}")
        return []

    match = _INT_ARRAY.search(raw_response or "")
    if not match:
        logging.info(f"Tag backfill response unparseable for '{value}' ({category}): {raw_response!r}")
        return []

    try:
        indices = json.loads(match.group(0))
    except ValueError:
        return []

    matched_paths = []
    for i in indices:
        if isinstance(i, int) and 1 <= i <= len(paths):
            matched_paths.append(paths[i - 1])
    return matched_paths


def apply_tag_to_songs(category: str, value: str, matched_paths: list[str]) -> int:
    """Add a tag value to every listed song that doesn't already have it.

    Shared by backfill_new_tags() and the admin-triggered rescan of an
    existing tag. Returns how many songs were actually updated.
    """
    updated = 0
    for path in matched_paths:
        tags = tag_manager.read_tags(path)
        values = tags.get(category) or []
        if value in values:
            continue
        tags[category] = values + [value]
        if tag_manager.write_tags(path, tags):
            updated += 1
    return updated


def backfill_new_tags(
    before_vocab: dict,
    applied_tags: dict,
    origin_title: str,
    other_songs: dict[str, str],
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
) -> dict[tuple[str, str], int]:
    """Find tags in applied_tags that didn't exist anywhere in the library before,
    and for each one, scan the rest of the library for songs that should also have it.

    Args:
        before_vocab: tag_manager.scan_library_vocab() result from BEFORE this write.
        applied_tags: The tag dict that was just written for one song.
        origin_title: Clean title of that song.
        other_songs: {song_path: clean_title} for every other song in the library.

    Returns:
        {(category, value): songs_updated_count} for every tag that was backfilled.
    """
    results: dict[tuple[str, str], int] = {}

    for category in tag_manager.CATEGORY_KEYS:
        existing_lower = {v.lower() for v in before_vocab.get(category, [])}
        for value in applied_tags.get(category) or []:
            if value.lower() in existing_lower:
                continue  # not actually new

            matched_paths = backfill_tag_across_library(
                category, value, origin_title, other_songs,
                model=model, base_url=base_url, timeout=timeout,
            )
            if not matched_paths:
                continue

            count = apply_tag_to_songs(category, value, matched_paths)
            if count:
                results[(category, value)] = count

    return results
