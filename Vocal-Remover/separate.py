#!/usr/bin/env python3
"""Simple helper script that uses yt-dlp to download an audio file from a
URL and then applies the ``vocal-remover`` library to split the file into
vocal and backing‑track stems.

The script is intentionally minimal; it downloads to an MP3 using youtube‑dl
and then writes two WAV files alongside the downloaded track named
``<base>_vocals.wav`` and ``<base>_music.wav``.  Because the built-in vocal
output is sometimes weak, it also creates a third file ``<base>_vocals_subtract.wav``
which is simply the original audio minus the accompaniment.  Users can point
``--output-dir`` if they prefer to control where the intermediate file is stored.
"""

import argparse
import os
import subprocess
from pathlib import Path

from vocal_remover.model import Separator


def download_audio(url: str, output_template: str) -> Path:
    """Download ``url`` to an MP3 using yt-dlp.

    ``output_template`` should be a path template suitable for yt-dlp; the
    default example used by this script is ``"%(title)s.%(ext)s"`` and the
    resulting file path is returned.
    """
    command = [
        "yt-dlp",
        "--quiet",
        "--no-warnings",
        "--extract-audio",
        "--audio-format",
        "mp3",
        "-o",
        output_template,
        url,
    ]
    subprocess.run(command, check=True)

    # ``yt-dlp`` will expand the template; we're not able to know the exact
    # filename in advance.  Return the most recently modified MP3 in the
    # output directory as a heuristic.
    out_dir = Path(output_template).parent or Path(".")
    mp3s = sorted(out_dir.glob("*.mp3"), key=os.path.getmtime)
    if not mp3s:
        raise RuntimeError("yt-dlp did not produce any mp3 file")
    return mp3s[-1]


def separate(input_file: Path) -> tuple[Path, Path, Path]:
    """Run ``vocal_remover`` on ``input_file`` and return three paths.

    The library will produce ``accompaniment.wav`` and ``vocals.wav``; those
    are returned as ``music`` and ``vocals_lib``.  Because the library vocals
    are sometimes weak, we also create ``vocals_sub`` by loading the original
    file and subtracting the accompaniment from it.
    """
    sep = Separator()
    outdir = input_file.parent
    # ``split`` writes two files into ``outdir``
    sep.split(str(input_file), str(outdir))

    music = outdir / "accompaniment.wav"
    vocals_lib = outdir / "vocals.wav"

    # compute vocals by subtracting accompaniment from original
    try:
        import librosa
        import soundfile as sf
        sr = 44100
        orig, _ = librosa.load(str(input_file), sr=sr, mono=False, dtype="float32")
        acc, _ = librosa.load(str(music), sr=sr, mono=False, dtype="float32")
        # make lengths match
        if orig.shape != acc.shape:
            minlen = min(orig.shape[1], acc.shape[1])
            orig = orig[:, :minlen]
            acc = acc[:, :minlen]
        vocals_audio = orig - acc
        vocals_sub = outdir / (input_file.stem + "_vocals_subtract.wav")
        sf.write(str(vocals_sub), vocals_audio.T, sr)
    except Exception as exc:  # pragma: no cover - nonessential
        vocals_sub = None
        print(f"warning: unable to compute subtraction vocals: {exc}")

    return vocals_lib, music, vocals_sub


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download an audio URL and split vocals/backing track"
    )
    parser.add_argument("url", help="URL to download (yt-dlp supported)")
    parser.add_argument(
        "--output-dir",
        "-o",
        default=".",
        help="Directory where the MP3 (and final stems) will be placed",
    )
    args = parser.parse_args()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    template = str(outdir / "%(title)s.%(ext)s")

    mp3_path = download_audio(args.url, template)
    vocals_lib, music, vocals_sub = separate(mp3_path)

    print(f"downloaded      : {mp3_path}")
    print(f"vocals (library): {vocals_lib}")
    print(f"backing track   : {music}")
    if vocals_sub is not None:
        print(f"vocals (subtract): {vocals_sub}")


if __name__ == "__main__":
    main()
