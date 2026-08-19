#!/usr/bin/env python3
"""
Upload a generated kids Short to YouTube via the Data API v3.

Reuses the same OAuth refresh-token flow as scripts/sync_vods.py.

Required env vars (same as the Twitch sync):
  YOUTUBE_CLIENT_ID
  YOUTUBE_CLIENT_SECRET
  YOUTUBE_REFRESH_TOKEN

Optional env vars:
  YOUTUBE_PRIVACY     "public" | "unlisted" | "private"  (default: public for Shorts)
  YOUTUBE_PLAYLIST_ID playlist to add the upload to (optional)
  MADE_FOR_KIDS       "true" (default) or "false"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

# YouTube category IDs:
#   1 Film & Animation  2 Autos & Vehicles 10 Music        15 Pets & Animals
#  17 Sports           19 Travel & Events  20 Gaming       22 People & Blogs
#  23 Comedy           24 Entertainment    25 News/Pol     26 Howto & Style
#  27 Education        28 Science & Tech   _
CATEGORY_EDUCATION = "27"
CATEGORY_ENTERTAINMENT = "24"


def env(name: str, required: bool = True, default: str | None = None) -> str | None:
    v = os.environ.get(name, default)
    if required and not v:
        sys.exit(f"Missing required env var: {name}")
    return v


def youtube_service():
    creds = Credentials(
        token=None,
        refresh_token=env("YOUTUBE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=env("YOUTUBE_CLIENT_ID"),
        client_secret=env("YOUTUBE_CLIENT_SECRET"),
        scopes=YOUTUBE_SCOPES,
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_short(meta: dict) -> dict:
    """Upload a short from a metadata dict (as produced by generate_short.build_short)."""
    youtube = youtube_service()

    privacy = os.environ.get("YOUTUBE_PRIVACY", "public")
    playlist_id = os.environ.get("YOUTUBE_PLAYLIST_ID")
    made_for_kids = os.environ.get("MADE_FOR_KIDS", "true").lower() == "true"
    # Shorts are 9:16 vertical and usually category "Entertainment" or "Education"
    category = CATEGORY_ENTERTAINMENT

    body = {
        "snippet": {
            "title": meta["title"][:100],
            "description": meta["description"][:5000],
            "tags": meta.get("tags", [])[:500],
            "categoryId": category,
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": made_for_kids,
            "license": "youtube",
        },
    }

    video_path = meta["video_path"]
    if not os.path.exists(video_path):
        sys.exit(f"Video file not found: {video_path}")

    print(f"Uploading '{meta['title']}' ({meta['id']}) from {video_path}")
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True,
                            mimetype="video/mp4")
    req = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )
    response = None
    while response is None:
        status, response = req.next_chunk()
        if status:
            print(f"  upload {int(status.progress()*100)}%")
    video_id = response["id"]
    url = f"https://youtu.be/{video_id}"
    print(f"  UPLOADED: {url}")

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

    return {"youtube_id": video_id, "url": url}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("meta_json", help="Path to .json metadata file produced by generate_short.py")
    args = ap.parse_args()
    meta = json.loads(Path(args.meta_json).read_text())
    result = upload_short(meta)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
