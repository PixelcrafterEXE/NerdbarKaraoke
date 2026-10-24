"""Shared client for talking to the local Ollama instance.

Used by both llm_renamer (song title cleanup) and tag_suggester (genre/mood/
language/duet tagging) — the same Ollama service (also used by OpenWebUI) and
the same model serve both jobs.
"""

from __future__ import annotations

import os

import requests

# The Ollama service is reachable from this host/container at this address
# (it's a Kubernetes ClusterIP also used by OpenWebUI). Override via env vars
# if the deployment changes.
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://10.43.91.244:11434")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen:14b")
DEFAULT_TIMEOUT = float(os.environ.get("OLLAMA_RENAME_TIMEOUT", "25"))


def call_ollama(
    prompt: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    temperature: float = 0.1,
) -> str:
    """POST a prompt to Ollama's /api/generate and return the raw response text."""
    model = model or DEFAULT_OLLAMA_MODEL
    base_url = base_url or DEFAULT_OLLAMA_URL
    timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

    response = requests.post(
        base_url.rstrip("/") + "/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return data.get("response", "")
