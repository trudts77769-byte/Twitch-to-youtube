#!/usr/bin/env python3
"""
Pipeline script: generate + upload the next pending kids Short.

Designed to run on GitHub Actions on a schedule. Steps:
  1. Load state from state/uploaded_shorts.json to avoid re-uploads.
  2. Pick the next puzzle that hasn't been uploaded.
  3. Generate the 30s vertical MP4 + TTS voiceover + background music.
  4. Upload to YouTube as a public (or YOUTUBE_PRIVACY) Short, marked "Made for Kids".
  5. Record upload metadata in state/uploaded_shorts.json.

Env vars (same as Twitch sync):
  YOUTUBE_CLIENT_ID       required
  YOUTUBE_CLIENT_SECRET   required
  YOUTUBE_REFRESH_TOKEN   required
Optional:
  YOUTUBE_PRIVACY         default "public" (for kids shorts)
  YOUTUBE_PLAYLIST_ID     optional playlist to add to
  MADE_FOR_KIDS           default "true"
  PUZZLE_ID               if set, force-render a specific puzzle id (debug)
  SHORTS_OUTPUT_DIR       default "shorts_output"
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure scripts/ is on sys.path for sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from puzzles import next_puzzle, get_puzzle, ALL_PUZZLES
from generate_short import build_short, OUTPUT_DIR
from upload_short import upload_short

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "state" / "uploaded_shorts.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"uploaded": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def main():
    state = load_state()
    uploaded = state.setdefault("uploaded", {})

    puzzle_id = os.environ.get("PUZZLE_ID")
    if puzzle_id:
        puzzle = get_puzzle(puzzle_id)
        if not puzzle:
            sys.exit(f"Unknown PUZZLE_ID: {puzzle_id}")
    else:
        puzzle = next_puzzle(set(uploaded.keys()))
        if puzzle is None:
            print(f"All {len(ALL_PUZZLES)} puzzles have been uploaded. "
                  "Add more to scripts/puzzles.py!")
            return

    print(f"=== Building short for puzzle: {puzzle['id']} ({puzzle['type']}) ===")
    output_dir = Path(os.environ.get("SHORTS_OUTPUT_DIR", OUTPUT_DIR))
    output_dir.mkdir(parents=True, exist_ok=True)

    meta = build_short(puzzle, output_dir=output_dir)
    print(f"Built video: {meta['video_path']}")

    print("=== Uploading to YouTube ===")
    result = upload_short(meta)

    uploaded[puzzle["id"]] = {
        "youtube_id": result["youtube_id"],
        "url": result["url"],
        "title": meta["title"],
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "puzzle_type": puzzle["type"],
    }
    save_state(state)
    print(f"=== Done. State saved. {len(uploaded)}/{len(ALL_PUZZLES)} shorts uploaded. ===")

    # Clean up temp work directories
    for d in output_dir.glob(f"short_{puzzle['id']}_*"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
