#!/usr/bin/env python3
"""
scripts/post_shorts.py

Orchestrates generating a kids brain-teaser Short and uploading it to YouTube.

- Picks the next un-uploaded puzzle from scripts/puzzles.py (or a specific --puzzle-id).
- Generates the 30s vertical 1080x1920 MP4 via scripts/generate_short.py (build_short).
- Uploads to YouTube using the same OAuth refresh-token flow as scripts/sync_vods.py,
  with retries + exponential backoff on transient failures.
- Records the upload atomically in state/uploaded_shorts.json so each puzzle posts once.

Behavior:
 - Exit 0 on success, non-zero on error.
 - Supports --puzzle-id, --privacy, --dry-run, --verbose
 - Never prints secrets.

Required env vars (same as the Twitch-to-YouTube sync):
  YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN

Optional env vars:
  YOUTUBE_PRIVACY           public | unlisted | private  (default: public for Shorts)
  YOUTUBE_SHORTS_PLAYLIST_ID  playlist to add uploads to
  MADE_FOR_KIDS             "true" (default) | "false"
  PUZZLE_ID                 override puzzle id (equivalent to --puzzle-id)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# Make sibling imports work whether run as a script or module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from puzzles import ALL_PUZZLES, get_puzzle, next_puzzle  # noqa: E402
from generate_short import build_short, OUTPUT_DIR      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "uploaded_shorts.json"
UPLOAD_RETRIES = 5
RETRY_BACKOFF_BASE = 2.0  # seconds

# YouTube category ids
CATEGORY_ENTERTAINMENT = "24"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


# --------------------------------------------------------------------------- #
# Logging / args
# --------------------------------------------------------------------------- #
def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render and upload a YouTube Short.")
    p.add_argument("--puzzle-id", help="Puzzle id to render (omit for next in queue)",
                   default=None)
    p.add_argument("--privacy", choices=["public", "unlisted", "private"],
                   help="Privacy override")
    p.add_argument("--dry-run", action="store_true",
                   help="Render only; do not upload to YouTube")
    p.add_argument("--verbose", action="store_true", help="Verbose logging")
    return p.parse_args()


# --------------------------------------------------------------------------- #
# State (atomic writes)
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logging.warning("Failed to read state file %s: %s — starting fresh",
                            STATE_PATH, e)
            return {"uploaded": {}}
    return {"uploaded": {}}


def save_state_atomic(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="uploaded_shorts_", dir=str(STATE_PATH.parent))
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True,
                                       ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(STATE_PATH)
        logging.debug("State saved to %s", STATE_PATH)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def uploaded_ids(state: dict) -> set[str]:
    uploaded = state.get("uploaded", {})
    if isinstance(uploaded, dict):
        return set(uploaded.keys())
    # Back-compat: if stored as a list
    return {item.get("puzzle_id") for item in uploaded if isinstance(item, dict)}


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def pick_puzzle(puzzle_id: str | None, state: dict) -> dict:
    if puzzle_id:
        puzzle = get_puzzle(puzzle_id)
        if not puzzle:
            raise SystemExit(f"Unknown puzzle id: {puzzle_id}")
        return puzzle
    done = uploaded_ids(state)
    puzzle = next_puzzle(done)
    if puzzle is None:
        raise SystemExit(
            f"All {len(ALL_PUZZLES)} puzzles have been uploaded. "
            "Add more to scripts/puzzles.py!")
    return puzzle


def render_puzzle(puzzle: dict) -> dict:
    """Call into generate_short.build_short; return metadata dict."""
    logging.info("Rendering puzzle %s (%s)...", puzzle["id"], puzzle["type"])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # DRY_RENDER_ONLY env var lets us render silent video without TTS (network-free)
    if os.environ.get("DRY_RENDER_ONLY"):
        from generate_short import render_video
        video_path = OUTPUT_DIR / f"{puzzle['id']}.mp4"
        render_video(puzzle, video_path, audio_path=None)
        from generate_short import _title, _description, _tags
        return {
            "id": puzzle["id"],
            "title": _title(puzzle),
            "description": _description(puzzle),
            "tags": _tags(puzzle),
            "video_path": str(video_path),
            "type": puzzle["type"],
            "made_for_kids": True,
        }
    return build_short(puzzle, output_dir=OUTPUT_DIR)


# --------------------------------------------------------------------------- #
# YouTube
# --------------------------------------------------------------------------- #
def build_credentials_from_env() -> Credentials:
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        raise RuntimeError(
            "Missing YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / "
            "YOUTUBE_REFRESH_TOKEN in environment.")
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=YOUTUBE_SCOPES,
    )
    creds.refresh(GoogleRequest())
    logging.debug("Obtained access token; expires: %s", getattr(creds, "expiry", None))
    return creds


def _privacy_status(args: argparse.Namespace) -> str:
    if args.privacy:
        return args.privacy
    return os.environ.get("YOUTUBE_PRIVACY", "public")


def upload_video(youtube, meta: dict, privacy: str, made_for_kids: bool,
                 playlist_id: str | None) -> dict:
    """Upload with retries + exponential backoff on 5xx / transient errors."""
    file_path = Path(meta["video_path"])
    if not file_path.exists():
        raise RuntimeError(f"Video file not found: {file_path}")

    media = MediaFileUpload(str(file_path), chunksize=-1, resumable=True)
    body = {
        "snippet": {
            "title": meta["title"][:100],
            "description": meta["description"][:5000],
            "tags": meta.get("tags", [])[:500],
            "categoryId": CATEGORY_ENTERTAINMENT,
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": bool(made_for_kids),
        },
    }

    attempt = 0
    last_err: Exception | None = None
    while attempt <= UPLOAD_RETRIES:
        try:
            logging.info("Upload attempt %d for %s", attempt + 1, file_path.name)
            request = youtube.videos().insert(
                part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logging.debug("  upload %d%%", int(status.progress() * 100))
            video_id = response["id"]
            logging.info("Uploaded: https://youtu.be/%s", video_id)

            if playlist_id:
                try:
                    youtube.playlistItems().insert(
                        part="snippet",
                        body={
                            "snippet": {
                                "playlistId": playlist_id,
                                "resourceId": {"kind": "youtube#video",
                                               "videoId": video_id},
                            }
                        },
                    ).execute()
                    logging.info("Added to playlist %s", playlist_id)
                except HttpError as e:
                    logging.warning("Could not add to playlist: %s", e)
            return response
        except HttpError as e:
            code = getattr(e, "status_code", None) or (
                e.resp.status if hasattr(e, "resp") else None)
            last_err = e
            if code and 500 <= code < 600 and attempt < UPLOAD_RETRIES:
                backoff = RETRY_BACKOFF_BASE ** (attempt + 1)
                logging.warning("HTTP %s — retrying in %.1fs", code, backoff)
                time.sleep(backoff)
                attempt += 1
                # Reset the upload media for a fresh attempt
                media = MediaFileUpload(str(file_path), chunksize=-1, resumable=True)
                continue
            raise
        except (requests.exceptions.RequestException, OSError, TimeoutError) as e:
            last_err = e
            if attempt < UPLOAD_RETRIES:
                backoff = RETRY_BACKOFF_BASE ** (attempt + 1)
                logging.warning("Transient error (%s) — retrying in %.1fs", e, backoff)
                time.sleep(backoff)
                attempt += 1
                media = MediaFileUpload(str(file_path), chunksize=-1, resumable=True)
                continue
            raise
    # Should never reach here
    raise RuntimeError(f"Upload failed after {UPLOAD_RETRIES} retries: {last_err}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)

    state = load_state()
    puzzle_id = args.puzzle_id or os.environ.get("PUZZLE_ID")
    puzzle = pick_puzzle(puzzle_id, state)

    # Don't re-render / re-upload a puzzle that's already posted unless forced.
    if puzzle["id"] in uploaded_ids(state) and not args.puzzle_id:
        logging.info("Puzzle %s already uploaded; skipping.", puzzle["id"])
        return 0

    try:
        meta = render_puzzle(puzzle)
    except Exception:
        logging.exception("Render failed for %s", puzzle["id"])
        return 3

    if args.dry_run:
        logging.info("DRY RUN — would upload %s (title: %s)",
                     meta["video_path"], meta["title"])
        # Clean up rendered file in dry-run to keep runner tidy? We keep it so
        # the operator can inspect, but remove it in CI by uncommenting:
        # Path(meta["video_path"]).unlink(missing_ok=True)
        return 0

    try:
        creds = build_credentials_from_env()
    except Exception:
        logging.exception("Failed to build YouTube credentials.")
        return 5

    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    privacy = _privacy_status(args)
    playlist_id = os.environ.get("YOUTUBE_SHORTS_PLAYLIST_ID") or os.environ.get(
        "YOUTUBE_PLAYLIST_ID")
    made_for_kids = os.environ.get("MADE_FOR_KIDS", "true").lower() in (
        "1", "true", "yes", "on")

    try:
        response = upload_video(youtube, meta, privacy, made_for_kids, playlist_id)
    except Exception:
        logging.exception("YouTube upload failed.")
        return 6

    video_id = response.get("id")
    entry = {
        "youtube_id": video_id,
        "url": f"https://youtu.be/{video_id}",
        "title": meta["title"],
        "type": puzzle["type"],
        "privacy": privacy,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    state.setdefault("uploaded", {})[puzzle["id"]] = entry

    try:
        save_state_atomic(state)
    except Exception:
        logging.exception("Failed to persist state — video %s IS uploaded but "
                          "will be re-uploaded next run unless state is fixed.",
                          video_id)
        return 7

    # Cleanup working dirs (leave final mp4 for inspection; delete build tmp)
    for d in OUTPUT_DIR.glob(f"short_{puzzle['id']}_*"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)

    logging.info("Done — %s uploaded as %s", puzzle["id"], entry["url"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
