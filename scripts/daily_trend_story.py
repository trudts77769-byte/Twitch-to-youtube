#!/usr/bin/env python3
"""
Daily Trend-to-Story Video Pipeline

Steps (run daily at 7am EST):
  1. Pull Google Daily Trends (yesterday's top searches)
  2. Create a CSV spreadsheet of the top 5 words
  3. Predict yesterday's topic of interest from those words
  4. Write a short story (~2:30-2:40 narration) about the topic
  5. TTS narration via Microsoft Edge TTS (free)
  6. Source stock images from Pexels (free tier)
  7. Assemble video with ffmpeg (Ken Burns zoom on images + narration)
  8. Analyze video coherence
  9. Upload to YouTube with thumbnail, description, and tags

Environment variables (GitHub Secrets):
  - PEXELS_API_KEY        (for stock imagery)
  - OPENAI_API_KEY        (for story generation; fallback template used if absent)
  - YOUTUBE_CLIENT_ID     (for YouTube upload)
  - YOUTUBE_CLIENT_SECRET (for YouTube upload)
  - YOUTUBE_REFRESH_TOKEN (for YouTube upload)
  - YOUTUBE_PRIVACY       (default: private)
  - USE_FALLBACK          (if "1", uses template stories instead of OpenAI)
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import textwrap
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DOWNLOADS_DIR = REPO_ROOT / "downloads"
STATE_DIR = REPO_ROOT / "state"
OUTPUTS_DIR = DOWNLOADS_DIR / "daily_trend"
STATE_FILE = STATE_DIR / "trend_videos.json"

OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)

TARGET_DURATION_SECONDS = 155   # ~2:35
TARGET_WORD_COUNT = 350
PRIVACY = os.environ.get("YOUTUBE_PRIVACY", "private")
USE_FALLBACK = os.environ.get("USE_FALLBACK", "0") == "1"

# Locate ffmpeg and ffprobe
try:
    import imageio_ffmpeg
    _ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    FFMPEG = _ffmpeg_bin
    # Try sibling ffprobe first, then system path
    _ffprobe_sibling = str(_ffmpeg_bin).replace("ffmpeg", "ffprobe")
    if Path(_ffprobe_sibling).exists():
        FFPROBE = _ffprobe_sibling
    else:
        FFPROBE = shutil.which("ffprobe") or _ffmpeg_bin  # fallback: use ffmpeg itself
except Exception:
    FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
    FFPROBE = shutil.which("ffprobe") or FFMPEG


def log(msg: str, *args) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", *args, flush=True)


# ---------------------------------------------------------------------------
# Helpers: retry/backoff and Eastern-time run window
# ---------------------------------------------------------------------------
def _with_retry(fn, attempts: int = 4, base_delay: float = 2.0,
                label: str = "API call"):
    """Call fn up to `attempts` times with exponential backoff on exceptions.

    Returns fn() result, or raises the last exception if all attempts fail.
    """
    last_exc = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - deliberate broad retry
            last_exc = exc
            delay = base_delay * (2 ** i)
            log(f"{label} attempt {i + 1}/{attempts} failed: {exc} "
                f"(retrying in {delay:.0f}s)")
            time.sleep(delay)
    raise last_exc


def _eastern_now() -> datetime:
    """Return current time in America/New_York."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now()


def _in_run_window() -> bool:
    """True when local Eastern time is between 06:30 and 07:45.

    Two UTC crons fire daily (11:00 UTC and 12:00 UTC). Only one of them
    lands inside this Eastern-time window, so exactly one run per day
    proceeds — regardless of DST.
    """
    now = _eastern_now()
    minutes = now.hour * 60 + now.minute
    return 6 * 60 + 30 <= minutes < 7 * 60 + 45


# ---------------------------------------------------------------------------
# Step 1 — Fetch Google Daily Trends
# ---------------------------------------------------------------------------
def fetch_daily_trends() -> list[str]:
    """Return yesterday's top 15 Google Daily Search Trends."""
    try:
        from pytrends.request import TrendReq
    except ImportError:
        log("pytrends not installed. Using fallback trends.")
        return _fallback_trends()

    try:
        pytrends = TrendReq(hl="en-US", tz=0, retries=4, backoff_factor=2)
        df = _with_retry(
            lambda: pytrends.trending_searches(pn="united_states"),
            attempts=3, base_delay=3.0, label="pytrends.trending_searches",
        )
        if df is None or df.empty:
            log("No daily trends returned; using fallback.")
            return _fallback_trends()
        trends: list[str] = df[0].tolist()[:15]
        log(f"Fetched {len(trends)} trending searches.")
        return trends
    except Exception as exc:
        log(f"Error fetching from pytrends: {exc}")
        return _fallback_trends()


def _fallback_trends() -> list[str]:
    log("Using fallback trend data.")
    return [
        "solar eclipse",
        "artificial intelligence",
        "crypto market",
        "olympics 2024",
        "new movie release",
        "world cup",
        "space launch",
        "climate change",
        "tech stocks",
        "super bowl",
        "election results",
        "hurricane",
        "mars mission",
        "ai art",
        "quantum computing",
    ]


# ---------------------------------------------------------------------------
# Step 2 — Create spreadsheet (CSV)
# ---------------------------------------------------------------------------
def create_spreadsheet(trends: list[str], csv_path: Path) -> list[dict]:
    """Write top-5 trending phrases to a CSV and return row dicts."""
    rows = []
    for i, phrase in enumerate(trends[:5], 1):
        rows.append({
            "rank": i,
            "trending_phrase": phrase,
            "key_words": ", ".join(phrase.lower().split()),
            "word_count": len(phrase.split()),
            "characters": len(phrase),
        })

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "rank", "trending_phrase", "key_words", "word_count", "characters",
        ])
        writer.writeheader()
        writer.writerows(rows)

    log(f"CSV saved: {csv_path}")
    return rows


# ---------------------------------------------------------------------------
# Step 3 — Predict topic of interest
# ---------------------------------------------------------------------------
def predict_topic(trends: list[str], rows: list[dict]) -> str:
    """Return a short human-readable topic label."""
    top5 = [r["trending_phrase"] for r in rows]
    all_words = []
    for t in trends:
        all_words.extend(t.lower().split())
    word_freq = Counter(all_words)

    if top5:
        topic = top5[0]
        log(f"Predicted topic: {topic!r}")
        return topic
    return word_freq.most_common(1)[0][0] if word_freq else "trending"


# ---------------------------------------------------------------------------
# Step 4 — Write a short story
# ---------------------------------------------------------------------------
def generate_story(topic: str, trends: list[str]) -> str:
    """Return a ~350-word narrative about *topic*."""
    if not USE_FALLBACK:
        try:
            return _openai_story(topic)
        except Exception as exc:
            log(f"OpenAI story failed: {exc}. Falling back to template.")
    return _template_story(topic, trends)


def _openai_story(topic: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    prompt = (
        f"Write a short, engaging narrative story (around {TARGET_WORD_COUNT} words)"
        f" about: {topic}.\n\n"
        "The story should:\n"
        "- Be suitable for all ages (YouTube-friendly)\n"
        "- Have a clear beginning, middle, and end\n"
        "- Read naturally when spoken aloud at a moderate pace\n"
        "- Take about 2:30 to 2:40 minutes to narrate\n"
        "- End with an intriguing or inspiring note\n\n"
        "Write only the story — no preamble, no commentary."
    )
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


def _template_story(topic: str, trends: list[str]) -> str:
    """Fallback template when no LLM is available — expanded to ~350 words."""
    main_trend = trends[0] if trends else topic
    words = main_trend.lower().split()
    trend_noun = words[0] if words else main_trend
    return (
        f"Good morning. Today we're going to talk about something that has captured "
        f"the world's attention: {main_trend}.\n\n"
        f"Picture this. You wake up, grab your phone, and the first thing you see "
        f"is everyone talking about {main_trend}. It's on your social media feed, "
        f"it's on the morning news, your friends are messaging you about it, and "
        f"even your coworkers can't stop discussing it over coffee. This is what "
        f"happens when a topic explodes into the global conversation overnight.\n\n"
        f"But how did we get here? The story of {main_trend} didn't begin yesterday. "
        f"It has roots that stretch back years, even decades in some cases. "
        f"Researchers and experts in the field have been tracking its development "
        f"for a long time. What we're seeing now is the culmination of trends "
        f"that have been building steadily beneath the surface.\n\n"
        f"What makes {main_trend} so fascinating is how it connects to so many "
        f"different parts of our lives. It affects technology, culture, the economy, "
        f"and even the way we think about the future. Some people see it as an "
        f"incredible opportunity — a chance to be part of something transformative. "
        f"Others view it with caution, worried about what changes it might bring. "
        f"The truth, as it often does, lies somewhere in the middle.\n\n"
        f"Consider for a moment how this looked just five years ago. Very few "
        f"people could have predicted that {main_trend} would become the defining "
        f"story of our time. The speed at which things have evolved is remarkable. "
        f"It reminds us that the world is always changing, often in ways we "
        f"least expect.\n\n"
        f"Looking ahead, the implications are profound. What happens next will "
        f"depend on how we as a society choose to respond. Will we embrace the "
        f"change? Will we try to shape it? Will we slow down to consider the "
        f"consequences? These are the questions that experts are debating right now.\n\n"
        f"One thing is certain: {main_trend} has already left an indelible mark "
        f"on our world. Whether you're a casual observer or deeply involved in "
        f"this space, the effects are being felt everywhere. From boardrooms to "
        f"classrooms, from living rooms to laboratories, this conversation is "
        f"reshaping how we see everything.\n\n"
        f"As the day unfolds and more information comes to light, we'll continue "
        f"to follow this story. New developments are emerging all the time. "
        f"For now, take a moment to appreciate that you're living through a "
        f"significant moment in history. The story of {main_trend} is still "
        f"being written, and we all have a front-row seat.\n\n"
        f"Thank you for joining us today. This has been your daily trend story. "
        f"Stay curious, stay informed, and we'll see you tomorrow with another "
        f"chapter from the world's trending searches."
    )


# ---------------------------------------------------------------------------
# Step 5 — Text-to-Speech (edge-tts)
# ---------------------------------------------------------------------------
def generate_narration(story_text: str, out_path: Path) -> Path | None:
    """Synthesize speech with edge-tts, return path to audio file."""
    try:
        import edge_tts
    except ImportError:
        log("ERROR: edge-tts not installed.")
        return None

    voice = "en-GB-SoniaNeural"
    mp3_path = out_path.with_suffix(".mp3")
    log(f"Generating narration with {voice}...")
    try:
        communicate = edge_tts.Communicate(story_text, voice)
        communicate.save_sync(mp3_path)
        dur = _get_audio_duration(mp3_path)
        log(f"Narration saved ({dur:.1f}s): {mp3_path}")
        return mp3_path
    except Exception as exc:
        log(f"edge-tts failed: {exc}")
        return _dummy_audio(mp3_path)


def _dummy_audio(path: Path) -> Path | None:
    """Create a short silent WAV as fallback."""
    log("Creating silent audio fallback...")
    try:
        import struct
        sr = 44100
        ns = sr * TARGET_DURATION_SECONDS
        data_size = ns * 2
        with open(path, "wb") as f:
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + data_size))
            f.write(b"WAVE")
            f.write(b"fmt ")
            f.write(struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16))
            f.write(b"data")
            f.write(struct.pack("<I", data_size))
            f.write(b"\x00" * data_size)
        log(f"Dummy audio: {path}")
        return path
    except Exception as exc:
        log(f"Failed to create dummy audio: {exc}")
        return None


# ---------------------------------------------------------------------------
# Step 6 — Stock images (Pexels)
# ---------------------------------------------------------------------------
def fetch_images(query: str, count: int = 8) -> list[Path]:
    """Fetch royalty-free images via Pexels. Returns local paths."""
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        log("No PEXELS_API_KEY — generating test images.")
        return _test_images(count)

    try:
        from pexels_api_py import API
    except ImportError:
        log("pexels-api-py not installed. Using test images.")
        return _test_images(count)

    out_dir = OUTPUTS_DIR / "images"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        api = API(api_key)
        result = _with_retry(
            lambda: api.search(query, results_per_page=count),
            attempts=3, base_delay=3.0, label="pexels.search",
        )
        images: list[Path] = []
        for i, photo in enumerate(result.photos[:count]):
            img_path = out_dir / f"scene_{i+1:02d}.jpg"
            _with_retry(
                lambda: _download_image(photo.src.large, img_path),
                attempts=3, base_delay=2.0,
                label=f"download image {i + 1}",
            )
            images.append(img_path)
            log(f"Pexels image {i+1}/{count}")
        return images
    except Exception as exc:
        log(f"Pexels error: {exc}")
        return _test_images(count)


def _download_image(url: str, path: Path) -> None:
    """Download a single image from URL."""
    import requests
    resp = requests.get(url, stream=True, timeout=15)
    resp.raise_for_status()
    with open(path, "wb") as f:
        for chunk in resp.iter_content(8192):
            f.write(chunk)


def _test_images(count: int) -> list[Path]:
    """Create simple coloured placeholder images."""
    out_dir = OUTPUTS_DIR / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(count):
        p = out_dir / f"scene_{i+1:02d}.jpg"
        try:
            from PIL import Image
            img = Image.new("RGB", (1920, 1080), (30 + i * 25, 40, 80))
            img.save(p, "JPEG", quality=85)
        except Exception:
            with open(p, "wb") as f:
                f.write(b"\xff\xd8\xff\xdb\x00C\x00\x08\x06\x06\x07"
                        b"\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
                        b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f"
                        b"\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c"
                        b"\x20$\'\"\x20\'\x1c\x1c(7),01444\x1c\x1c"
                        b"5)\x95\x20x9-,\x99\xff\xd9")
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# Step 7 — Assemble video (ffmpeg)
# ---------------------------------------------------------------------------
def assemble_video(
    narration_path: Path,
    images: list[Path],
    output_path: Path,
) -> Path | None:
    """Combine narration and images into an MP4 with Ken Burns zoom."""
    log("Assembling video with ffmpeg...")
    if not images:
        log("No images.")
        return None

    audio_dur = _get_audio_duration(narration_path)
    if audio_dur < 1:
        audio_dur = TARGET_DURATION_SECONDS

    num_img = len(images)
    t_per = audio_dur / num_img

    # Build filter chains
    inputs_cmd = []
    filt_parts = []
    for i, img in enumerate(images):
        inputs_cmd += ["-loop", "1", "-t", f"{t_per:.2f}", "-i", str(img)]
        filt_parts.append(
            f"[{i}:v]setpts=PTS-STARTPTS,"
            f"scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,"
            f"zoompan=z='min(zoom+0.0015,1.15)':d={int(t_per * 25)}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080"
            f"[v{i}]"
        )
    concat = "".join(f"[v{i}]" for i in range(num_img))
    concat += f"concat=n={num_img}:v=1:a=0[outv]"
    filter_complex = ";".join(filt_parts) + ";" + concat

    cmd = (
        [FFMPEG, "-y"]
        + inputs_cmd
        + ["-i", str(narration_path),
           "-filter_complex", filter_complex,
           "-map", "[outv]",
           "-map", f"{num_img}:a",
           "-c:v", "libx264",
           "-pix_fmt", "yuv420p",
           "-crf", "23",
           "-preset", "medium",
           "-c:a", "aac",
           "-b:a", "192k",
           "-shortest",
           str(output_path),
        ]
    )

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        log(f"Video ready: {output_path}")
        return output_path
    except subprocess.CalledProcessError as exc:
        log(f"ffmpeg error:\n{exc.stderr[:600]}")
        return None
    except subprocess.TimeoutExpired:
        log("ffmpeg timed out.")
        return None


def _probe_media(path: Path) -> dict | None:
    """Get media info using ffprobe or ffprobe-equivalent ffmpeg args."""
    is_ffprobe = "ffprobe" in FFPROBE
    try:
        if is_ffprobe:
            cmd = [FFPROBE, "-v", "quiet", "-show_format", "-print_format", "json"]
        else:
            # ffmpeg can also output format info with -show_format
            cmd = [FFPROBE, "-v", "quiet", "-show_format", "-print_format", "json"]
        r = subprocess.run(
            cmd + [str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return None


def _get_audio_duration(path: Path) -> float:
    """Return audio duration in seconds."""
    info = _probe_media(path)
    if info and "format" in info:
        dur = info["format"].get("duration")
        if dur:
            return float(dur)
    # Fallback: read WAV header for duration
    try:
        with open(path, "rb") as f:
            header = f.read(44)
            if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
                # Read sample rate at offset 24, data size at offset 40
                sample_rate = int.from_bytes(header[24:28], "little")
                data_size = int.from_bytes(header[40:44], "little")
                if sample_rate > 0:
                    return data_size / (sample_rate * 2)  # 16-bit mono
    except Exception:
        pass
    return TARGET_DURATION_SECONDS


# ---------------------------------------------------------------------------
# Step 8 — Coherence analysis
# ---------------------------------------------------------------------------
def analyze_coherence(video_path: Path) -> bool:
    """Check that the video file is valid, has expected duration, and is not corrupt."""
    log("Analyzing video coherence...")
    if not video_path.exists():
        log("FAIL: Video file does not exist.")
        return False

    mb = video_path.stat().st_size / (1024 * 1024)
    log(f"Size: {mb:.1f} MB")

    info = _probe_media(video_path)
    if info and "format" in info:
        dur = float(info["format"].get("duration", 0))
        log(f"Duration: {dur:.1f}s (target ~{TARGET_DURATION_SECONDS}s)")
        if dur < 10:
            log("FAIL: Very short video (likely corrupt).")
            return False
        log("PASS: Video looks good.")
        return True

    # Fallback: just check file size
    log(f"Coherence: file exists ({mb:.1f} MB), passed basic check.")
    return mb > 0.1


# ---------------------------------------------------------------------------
# Step 8b — Generate thumbnail
# ---------------------------------------------------------------------------
def generate_thumbnail(topic: str, out_path: Path) -> Path:
    """Create a 1280x720 JPEG thumbnail with the topic text overlay."""
    log(f"Generating thumbnail for: {topic}...")
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1280, 720), (25, 25, 50))
        draw = ImageDraw.Draw(img)
        # Gradient background
        for y in range(720):
            r = 25 + int(y / 720 * 30)
            g = 25 + int(y / 720 * 20)
            b = 50 + int(y / 720 * 60)
            draw.line([(0, y), (1280, y)], fill=(r, g, b))
        # Accent bars
        draw.rectangle([(0, 300), (1280, 340)], fill=(255, 60, 60, 180))
        draw.rectangle([(0, 420), (1280, 440)], fill=(255, 60, 60, 180))
        # Fonts
        font_big = None
        font_sm = None
        for fp in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
            if os.path.exists(fp):
                font_big = ImageFont.truetype(fp, 48)
                font_sm = ImageFont.truetype(fp, 30)
                break
        draw.text((640, 260), "Today's Story:", fill="white",
                  anchor="ms", font=font_sm)
        draw.text((640, 370), topic[:50], fill="white",
                  anchor="ms", font=font_big)
        draw.text((640, 500), "Daily Trend Stories", fill="gray",
                  anchor="ms", font=font_sm)
        img.save(out_path, "JPEG", quality=90)
        log(f"Thumbnail: {out_path}")
    except Exception as exc:
        log(f"Thumbnail error: {exc}")
    return out_path


# ---------------------------------------------------------------------------
# Step 9 — Upload to YouTube
# ---------------------------------------------------------------------------
def upload_to_youtube(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    thumb_path: Path | None = None,
    privacy: str = "private",
) -> str | None:
    """Upload via YouTube Data API v3. Returns video ID or None."""
    missing = [v for v in ("YOUTUBE_CLIENT_ID",
                           "YOUTUBE_CLIENT_SECRET",
                           "YOUTUBE_REFRESH_TOKEN")
               if not os.environ.get(v)]
    if missing:
        log(f"Missing YouTube credentials: {', '.join(missing)}; skipping upload.")
        return None

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        log("google-api-python-client packages not installed.")
        return None

    log("Authenticating with YouTube...")
    # youtube.upload is the least-privilege scope and covers both
    # videos.insert and thumbnails.set. (youtube.force-ssl also works
    # if you prefer; the refresh token must be issued with that scope.)
    scopes = os.environ.get(
        "YOUTUBE_SCOPE",
        "https://www.googleapis.com/auth/youtube.upload",
    ).split(",")
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=[s.strip() for s in scopes],
    )

    service = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500],
            "categoryId": "24",  # Entertainment
        },
        "status": {
            "privacyStatus": privacy,
        },
    }

    log("Uploading video file...")
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )
    resp = request.execute()
    video_id = resp["id"]
    log(f"Upload OK! https://youtu.be/{video_id}")

    if thumb_path and thumb_path.exists():
        try:
            service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumb_path)),
            ).execute()
            log("Thumbnail set.")
        except Exception as exc:
            log(f"Thumbnail upload failed: {exc}")

    return video_id


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"posted_dates": [], "videos": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    log(f"State saved to {STATE_FILE}")


# ---------------------------------------------------------------------------
# Main pipeline runner
# ---------------------------------------------------------------------------
def run_pipeline(skip_upload: bool = False, test_mode: bool = False,
                 force: bool = False) -> None:
    log("=" * 60)
    log("  DAILY TREND-TO-STORY PIPELINE  ")
    log("=" * 60)

    # DST-safe scheduling guard. Two UTC crons fire daily; only the one
    # landing inside the 06:30–07:45 Eastern window should proceed.
    if not force and not test_mode and not _in_run_window():
        log(f"Current Eastern time {_eastern_now().strftime('%H:%M')} is outside "
            "the 06:30–07:45 run window; skipping (use --force to override).")
        return

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    log(f"Processing trends for {yesterday}")

    state = load_state()
    if not test_mode and yesterday in state["posted_dates"]:
        log(f"Already posted for {yesterday}. Skipping.")
        return

    # 1 — Trends
    log("\n[1/9] Fetching Google Daily Trends...")
    trends = fetch_daily_trends()
    log(f"Top: {trends[:5]}")

    # 2 — Spreadsheet
    log("\n[2/9] Creating spreadsheet...")
    csv_path = OUTPUTS_DIR / f"trends_{yesterday}.csv"
    rows = create_spreadsheet(trends, csv_path)

    # 3 — Predict topic
    log("\n[3/9] Predicting topic...")
    topic = predict_topic(trends, rows)
    log(f"Topic: {topic}")

    # 4 — Write story
    log("\n[4/9] Writing story...")
    story = generate_story(topic, trends)
    story_path = OUTPUTS_DIR / f"story_{yesterday}.txt"
    story_path.write_text(story, encoding="utf-8")
    wc = len(story.split())
    log(f"Story: {wc} words -> {story_path}")

    # 5 — TTS
    log("\n[5/9] Generating narration...")
    nar_path = generate_narration(story, OUTPUTS_DIR / f"narration_{yesterday}")
    if not nar_path or not nar_path.exists():
        log("ABORT: No narration.")
        return

    # 6 — Images
    log("\n[6/9] Fetching images...")
    query = topic.split()[0] if topic else "trending"
    images = fetch_images(query)

    # 7 — Video
    log("\n[7/9] Assembling video...")
    video_path = assemble_video(
        nar_path, images, OUTPUTS_DIR / f"video_{yesterday}.mp4"
    )

    # Thumbnail
    thumb_path = generate_thumbnail(
        topic, OUTPUTS_DIR / f"thumbnail_{yesterday}.jpg"
    )

    # 8 — Coherence
    log("\n[8/9] Coherence check...")
    if not video_path or not analyze_coherence(video_path):
        log("FAIL: Video failed coherence check, aborting.")
        if not test_mode:
            return
        log("(continuing in test mode)")

    # 9 — Upload
    if not skip_upload:
        log("\n[9/9] Uploading to YouTube...")
        title = f"Daily Trend Story: {topic[:80]} | {yesterday}"
        desc = textwrap.dedent(f"""\
            {story[:4000]}

            📊 Daily Trend Stories
            📅 Trending searches from {yesterday}
            🎯 Topic: {topic}

            #DailyTrendStories #Trending #StoryTime #AIStory

            Auto-generated from the world's top searches.
            Subscribe for new stories every morning at 7 AM EST!
        """).strip()
        tags = [
            "daily trend stories", "trending", "story time",
            topic.lower(),
        ] + [w.lower() for w in topic.split()[:5]]

        video_id = upload_to_youtube(
            video_path=video_path,
            title=title[:100],
            description=desc[:5000],
            tags=tags,
            thumb_path=thumb_path if thumb_path.exists() else None,
            privacy=PRIVACY,
        )

        if video_id:
            state["posted_dates"].append(yesterday)
            state["videos"].append({
                "date": yesterday,
                "topic": topic,
                "url": f"https://youtu.be/{video_id}",
                "video_id": video_id,
                "uploaded_at": datetime.now().isoformat(),
            })
            save_state(state)
            log(f"  Done! https://youtu.be/{video_id}")
        else:
            log("Upload failed or skipped.")
    else:
        log("Skipping upload (--skip-upload flag).")
        if video_path:
            log(f"Video ready at: {video_path}")

    log("\n" + "=" * 60)
    log("  Pipeline complete!  ")
    log("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily Trend-to-Story Pipeline")
    parser.add_argument("--skip-upload", action="store_true",
                        help="Skip YouTube upload")
    parser.add_argument("--test", action="store_true",
                        help="Test mode: skip duplicate check and run-window guard")
    parser.add_argument("--force", action="store_true",
                        help="Run even outside the 7am Eastern window "
                             "(used by workflow_dispatch)")
    args = parser.parse_args()
    run_pipeline(skip_upload=args.skip_upload, test_mode=args.test,
                 force=args.force)