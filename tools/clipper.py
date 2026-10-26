#!/usr/bin/env python3
"""Create ten captioned vertical clips from a permitted YouTube source."""

from __future__ import annotations

import argparse
import bisect
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}

HOOK_TERMS = {
    "actually",
    "because",
    "best",
    "biggest",
    "but",
    "crazy",
    "discover",
    "every",
    "first",
    "here's",
    "important",
    "mistake",
    "never",
    "only",
    "secret",
    "surprising",
    "truth",
    "why",
    "worst",
}


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class ClipCandidate:
    start: float
    end: float
    score: float
    text: str


def validate_youtube_url(url: str) -> None:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise ValueError("Enter an HTTPS YouTube video link (youtube.com or youtu.be).")
    if not parsed.path or parsed.path == "/":
        raise ValueError("The YouTube link is missing a video path.")


def score_window(words: list[Word], duration: float) -> float:
    """Rank a transcript window using free, local text features."""
    tokens = [
        token.lower()
        for word in words
        for token in re.findall(r"[\w']+", word.text, flags=re.UNICODE)
    ]
    if not tokens or duration <= 0:
        return 0.0

    words_per_minute = len(tokens) * 60 / duration
    rate_score = max(0.0, 1.0 - abs(words_per_minute - 145) / 145)
    lexical_variety = len(set(tokens)) / len(tokens)
    hook_count = sum(token in HOOK_TERMS for token in tokens)
    hook_score = min(1.0, hook_count / 3)
    punctuation_count = sum(
        word.text.rstrip().endswith((".", "?", "!")) for word in words
    )
    punctuation_score = min(1.0, punctuation_count / max(1, len(words) / 12))
    complete_ending = 1.0 if words[-1].text.rstrip().endswith((".", "?", "!")) else 0.0

    return round(
        50 * rate_score
        + 20 * lexical_variety
        + 15 * hook_score
        + 8 * punctuation_score
        + 7 * complete_ending,
        2,
    )


def select_clips(
    words: list[Word], video_duration: float, clip_count: int = 10
) -> list[ClipCandidate]:
    """Choose high-scoring, non-overlapping transcript windows."""
    if not words:
        raise ValueError("No speech was transcribed from this video.")

    words = sorted(words, key=lambda word: word.start)
    starts = [word.start for word in words]
    selected: list[ClipCandidate] = []

    # Prefer clips around 45 seconds; shorten the target only when needed to
    # find ten distinct segments in a shorter source.
    for target_length in (45.0, 35.0, 25.0, 18.0):
        candidates: list[ClipCandidate] = []
        grid_start = 0.0
        while grid_start + target_length <= video_duration:
            first = bisect.bisect_left(starts, grid_start)
            if first < len(words):
                clip_start = max(0.0, words[first].start - 0.12)
                clip_end = min(video_duration, clip_start + target_length)
                left = bisect.bisect_left(starts, clip_start)
                right = bisect.bisect_left(starts, clip_end)
                window = words[left:right]
                minimum_words = max(12, int(target_length * 0.65))
                if len(window) >= minimum_words:
                    candidates.append(
                        ClipCandidate(
                            start=clip_start,
                            end=clip_end,
                            score=score_window(window, clip_end - clip_start),
                            text=" ".join(word.text for word in window),
                        )
                    )
            grid_start += 5.0

        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        for candidate in candidates:
            overlaps = any(
                candidate.start < chosen.end - 1.0
                and candidate.end > chosen.start + 1.0
                for chosen in selected
            )
            if not overlaps:
                selected.append(candidate)
                if len(selected) == clip_count:
                    break
        if len(selected) == clip_count:
            break

    if len(selected) < clip_count:
        raise ValueError(
            f"Found only {len(selected)} distinct speech segments for {clip_count} clips. "
            "Try a longer video with more spoken content."
        )

    return sorted(selected[:clip_count], key=lambda candidate: candidate.score, reverse=True)


def ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole_seconds, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{whole_seconds:02}.{centis:02}"


def escape_ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def build_ass(words: list[Word], clip_start: float, clip_end: float) -> str:
    """Make readable word-timed ASS subtitles in the source language."""
    local_words = [
        Word(
            start=max(0.0, word.start - clip_start),
            end=min(clip_end - clip_start, word.end - clip_start),
            text=word.text.strip(),
        )
        for word in words
        if word.end > clip_start and word.start < clip_end and word.text.strip()
    ]
    if not local_words:
        raise ValueError("A selected clip has no subtitle words.")

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,68,&H00FFFFFF,&H0000D7FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,2,5,60,60,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    group: list[Word] = []

    def flush_group() -> None:
        if not group:
            return
        start = max(0.0, group[0].start - 0.04)
        end = min(clip_end - clip_start, group[-1].end + 0.18)
        karaoke: list[str] = []
        for word in group:
            duration_cs = max(1, round(max(0.04, word.end - word.start) * 100))
            karaoke.append(f"{{\\kf{duration_cs}}}{escape_ass_text(word.text)}")
        text = "{\\an5\\pos(540,1500)}" + " ".join(karaoke)
        events.append(
            f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},"
            f"Default,,0,0,0,,{text}"
        )
        group.clear()

    for word in local_words:
        current_text = " ".join(item.text for item in group)
        gap = word.start - group[-1].end if group else 0.0
        if group and (
            len(group) >= 4
            or len(current_text) + len(word.text) > 30
            or gap > 0.85
        ):
            flush_group()
        group.append(word)
    flush_group()

    return header + "\n".join(events) + "\n"


def download_video(url: str, download_dir: Path) -> tuple[Path, dict]:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    download_dir.mkdir(parents=True, exist_ok=True)
    options = {
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "outtmpl": str(download_dir / "source.%(ext)s"),
        "noplaylist": True,
        "restrictfilenames": True,
        "merge_output_format": "mp4",
        "max_filesize": 3 * 1024**3,
        "quiet": True,
        "no_warnings": False,
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as error:
        raise RuntimeError(
            "YouTube could not provide this video to the GitHub runner. "
            "The source may be restricted or YouTube may be blocking automated access. "
            f"Downloader message: {error}"
        ) from error

    files = [
        path
        for path in download_dir.glob("source.*")
        if path.is_file() and path.suffix not in {".part", ".ytdl", ".json"}
    ]
    if not files:
        raise RuntimeError("The download finished without producing a video file.")
    return max(files, key=lambda path: path.stat().st_size), info


def probe_video(path: Path) -> tuple[float, set[str]]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(result.stdout)
    duration = float(metadata.get("format", {}).get("duration", 0))
    media_types = {
        stream.get("codec_type", "")
        for stream in metadata.get("streams", [])
    }
    if duration <= 0 or "video" not in media_types:
        raise RuntimeError("The downloaded file does not contain a readable video stream.")
    if "audio" not in media_types:
        raise RuntimeError("The video has no audio track to transcribe.")
    return duration, media_types


def transcribe_video(path: Path, model_size: str) -> tuple[list[Word], str]:
    from faster_whisper import WhisperModel

    print(f"Transcribing on CPU with the Whisper {model_size} model...")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(path),
        word_timestamps=True,
        vad_filter=True,
    )
    words: list[Word] = []
    for segment in segments:
        segment_words = getattr(segment, "words", None) or []
        if segment_words:
            for item in segment_words:
                text = (item.word or "").strip()
                if text and item.start is not None and item.end is not None:
                    words.append(Word(float(item.start), float(item.end), text))
            continue

        # Preserve useful timing if a model segment lacks word-level timestamps.
        tokens = segment.text.split()
        span = max(0.0, float(segment.end) - float(segment.start))
        if tokens and span > 0:
            token_span = span / len(tokens)
            words.extend(
                Word(
                    float(segment.start) + index * token_span,
                    float(segment.start) + (index + 1) * token_span,
                    token,
                )
                for index, token in enumerate(tokens)
            )

    return words, getattr(info, "language", "unknown")


def render_clip(
    source: Path,
    output_dir: Path,
    words: list[Word],
    candidate: ClipCandidate,
    index: int,
) -> Path:
    subtitle_name = f"clip_{index:02}.ass"
    video_name = f"clip_{index:02}.mp4"
    (output_dir / subtitle_name).write_text(
        build_ass(words, candidate.start, candidate.end),
        encoding="utf-8",
    )
    filter_chain = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,ass=filename='{subtitle_name}'"
    )
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{candidate.start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{candidate.end - candidate.start:.3f}",
        "-vf",
        filter_chain,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        video_name,
    ]
    subprocess.run(command, check=True, cwd=output_dir)
    return output_dir / video_name


def write_manifest(
    output_dir: Path,
    source_title: str,
    language: str,
    candidates: list[ClipCandidate],
) -> Path:
    manifest = {
        "source_title": source_title,
        "subtitle_language": language,
        "selection_method": "local transcript heuristics; review clips before posting",
        "clips": [
            {
                "file": f"clip_{index:02}.mp4",
                "start_seconds": round(candidate.start, 2),
                "end_seconds": round(candidate.end, 2),
                "score": candidate.score,
                "opening_text": " ".join(candidate.text.split()[:12]),
            }
            for index, candidate in enumerate(candidates, start=1)
        ],
    }
    path = output_dir / "clip_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def create_zip(output_dir: Path, clip_paths: list[Path], manifest_path: Path) -> Path:
    archive_path = output_dir / "shorts_output.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in [*clip_paths, manifest_path]:
            archive.write(path, arcname=path.name)
    return archive_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create ten vertical, captioned MP4 clips from a YouTube video."
    )
    parser.add_argument("--url", required=True, help="HTTPS YouTube video URL")
    parser.add_argument("--model", choices=("base", "small"), default="base")
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument(
        "--confirm-rights",
        action="store_true",
        help="Confirm that you own the source or have permission to clip it.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        validate_youtube_url(args.url)
        if not args.confirm_rights:
            raise ValueError(
                "Confirm that you own the video or have permission to create clips from it."
            )
        args.output_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="youtube-clipper-") as temp:
            source, info = download_video(args.url, Path(temp) / "download")
            duration, _ = probe_video(source)
            print(f"Downloaded {info.get('title', 'video')} ({duration / 60:.1f} minutes).")
            words, language = transcribe_video(source, args.model)
            print(f"Transcribed {len(words)} words; detected language: {language}.")
            candidates = select_clips(words, duration, clip_count=10)

            clip_paths = [
                render_clip(source, args.output_dir, words, candidate, index)
                for index, candidate in enumerate(candidates, start=1)
            ]
            manifest = write_manifest(
                args.output_dir,
                str(info.get("title", "YouTube video")),
                language,
                candidates,
            )
            archive = create_zip(args.output_dir, clip_paths, manifest)

        print(f"Created {len(clip_paths)} clips: {archive}")
        return 0
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
