#!/usr/bin/env python3
"""
Twitch VOD -> YouTube uploader, designed to run on a schedule (e.g. GitHub Actions).

Flow:
  1. Get an app access token from Twitch.
  2. Look up the broadcaster id for TWITCH_USER_LOGIN.
  3. List recent VODs (type=archive).
  4. Skip any VOD id already in state/uploaded_vods.json.
  5. Download the new VOD with yt-dlp (if available), else fail clearly.
  6. Upload it to YouTube using the Data API v3 with a stored refresh token.
  7. Record the VOD id so we don't re-upload next run.

Required environment variables are documented in the README.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "state" / "uploaded_vods.json"
DOWNLOAD_DIR = ROOT / "downloads"

# Scopes needed to upload to YouTube.
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
                  "https://www.googleapis.com/auth/youtube"]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def env(name: str, required: bool = True) -> str | None:
    val = os.environ.get(name)
    if required and not val:
        sys.exit(f"Missing required environment variable: {name}")
    return val


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"uploaded": {}}  # {vod_id: {youtube_id, title, uploaded_at}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- #
# Twitch
# --------------------------------------------------------------------------- #
def twitch_token(client_id: str, client_secret: str) -> str:
    r = requests.post(
        "https://id.twitch.tv/oauth2/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def twitch_user_id(token: str, client_id: str, login: str) -> str:
    r = requests.get(
        "https://api.twitch.tv/helix/users",
        params={"login": login},
        headers={"Client-Id": client_id, "Authorization": f"Bearer {token}"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()["data"]
    if not data:
        sys.exit(f"Twitch user '{login}' not found.")
    return data[0]["id"]


def twitch_vods(token: str, client_id: str, broadcaster_id: str,
                limit: int = 10) -> list[dict]:
    r = requests.get(
        "https://api.twitch.tv/helix/videos",
        params={"user_id": broadcaster_id, "type": "archive", "first": limit},
        headers={"Client-Id": client_id, "Authorization": f"Bearer {token}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["data"]


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def download_vod(vod: dict) -> Path:
    """Use yt-dlp to grab the VOD. Returns the resulting file path."""
    if shutil.which("yt-dlp") is None:
        sys.exit("yt-dlp not found on PATH. "
                 "Install with: pip install yt-dlp  (and ffmpeg).")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(DOWNLOAD_DIR / f"{vod['id']}.%(ext)s")
    url = vod["url"]

    print(f"Downloading VOD {vod['id']}: {vod['title']} ({vod['url']})")
    cmd = [
        "yt-dlp",
        "--no-progress",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "-o", out_tmpl,
        url,
    ]
    subprocess.run(cmd, check=True)

    files = sorted(DOWNLOAD_DIR.glob(f"{vod['id']}.*"))
    if not files:
        sys.exit("Download finished but no output file found.")
    return files[0]


# --------------------------------------------------------------------------- #
# YouTube
# --------------------------------------------------------------------------- #
def youtube_service():
    client_id = env("YOUTUBE_CLIENT_ID")
    client_secret = env("YOUTUBE_CLIENT_SECRET")
    refresh_token = env("YOUTUBE_REFRESH_TOKEN")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=YOUTUBE_SCOPES,
    )
    creds.refresh(Request())  # exchanges refresh token for a fresh access token
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_to_youtube(vod: dict, file_path: Path) -> str:
    youtube = youtube_service()
    privacy = os.environ.get("YOUTUBE_PRIVACY", "private")
    playlist_id = os.environ.get("YOUTUBE_PLAYLIST_ID")

    title = vod["title"][:100]  # YouTube title max length
    description = (
        f"Originally streamed live on Twitch: {vod['url']}\n"
        f"Streamed: {vod['created_at']}\n"
    )

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["Twitch", "Livestream", vod.get("game_name", "")],
            "categoryId": "20",  # Gaming
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(file_path), chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    response = None
    print("Uploading to YouTube...")
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"  upload {int(status.progress() * 100)}%")
    video_id = response["id"]
    print(f"  -> https://youtu.be/{video_id}")

    if playlist_id:
        try:
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
            print(f"  added to playlist {playlist_id}")
        except HttpError as e:
            print(f"  WARNING: could not add to playlist: {e}")

    return video_id


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    twitch_client_id = env("TWITCH_CLIENT_ID")
    twitch_client_secret = env("TWITCH_CLIENT_SECRET")
    twitch_login = env("TWITCH_USER_LOGIN")

    state = load_state()
    uploaded = state["uploaded"]

    token = twitch_token(twitch_client_id, twitch_client_secret)
    broadcaster_id = twitch_user_id(token, twitch_client_id, twitch_login)
    vods = twitch_vods(token, twitch_client_id, broadcaster_id)

    # Most recent first; only process ones we haven't seen.
    new_vods = [v for v in vods if v["id"] not in uploaded]
    print(f"Found {len(vods)} recent VODs; {len(new_vods)} new.")

    if not new_vods:
        return

    for vod in new_vods:
        try:
            file_path = download_vod(vod)
            yt_id = upload_to_youtube(vod, file_path)
            uploaded[vod["id"]] = {
                "youtube_id": yt_id,
                "title": vod["title"],
                "url": vod["url"],
                "created_at": vod["created_at"],
            }
            save_state(state)
        finally:
            # Free runner disk space
            if DOWNLOAD_DIR.exists():
                shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
