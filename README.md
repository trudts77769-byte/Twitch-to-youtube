# Twitch-to-YouTube + Auto-Generated Kids Shorts

This repo runs two independent GitHub-Actions pipelines that upload to the same
YouTube channel via the YouTube Data API v3, using OAuth credentials stored as
GitHub repository secrets.

1. **Twitch → YouTube re-upload** (`scripts/sync_vods.py`) — the original
   automation: copies your Twitch VODs over to YouTube on a 6-hour schedule.
2. **Kids Brain-Teaser Shorts** (`scripts/post_shorts.py`) — generates
   original **30-second vertical (9:16) Shorts** aimed at kids 6–14 and posts
   them on a daily schedule. Topics: "odd one out" shape puzzles, quick math
   riddles, and word-brain teasers.

---

## 1. Twitch VOD → YouTube (unchanged)

Docs below in *Setup* cover the Twitch side.

### How it works

1. Scheduled workflow (`.github/workflows/twitch-to-youtube.yml`) runs every 6 h.
2. `scripts/sync_vods.py` asks Twitch Helix for your recent archive VODs.
3. Any VOD not yet in `state/uploaded_vods.json` is downloaded with `yt-dlp`
   and uploaded to YouTube.
4. The VOD id is recorded in `state/uploaded_vods.json` and committed back.

---

## 2. Auto-Generated Kids Shorts 🧩

### How it works

1. Scheduled workflow (`.github/workflows/kids-shorts.yml`) runs once per day at
   **17:00 UTC** (≈ after-school / after-dinner viewing window in the US).
   You can also trigger it manually from the Actions tab.
2. `scripts/post_shorts.py` picks the next brain-teaser puzzle from
   `scripts/puzzles.py` that isn't in `state/uploaded_shorts.json`.
3. `scripts/generate_short.py` renders a **30-second 1080×1920 (9:16) MP4**:
   - `0:00–0:03` Hook slide — "Can you find the odd one out?" (animated)
   - `0:03–0:13` Puzzle screen with a countdown timer (10 seconds to solve)
   - `0:13–0:20` Answer reveal (yellow highlight on the correct item +
     explanation)
   - `0:20–0:30` "Nice job!" CTA with LIKE / SUBSCRIBE buttons and confetti
4. A cheerful voiceover is synthesized with **edge-tts** (free, online — uses
   Microsoft's neural voices) and a soft chiptune-style background bed is
   generated on the fly with ffmpeg sine waves (no external music, so no
   copyright strikes).
5. The finished video is uploaded to YouTube as a **public Short**, category
   **Entertainment**, tagged as **Made for Kids** (COPPA compliant), with
   kid-friendly tags (`#shorts`, `#puzzle`, `#braingames`, `#forkids`, …).
6. The upload id is written to `state/uploaded_shorts.json` and committed so
   each puzzle posts exactly once.

### Adding more puzzles

To extend the catalog, add new entries to the `ALL_PUZZLES` list in
`scripts/puzzles.py`. There are three supported puzzle types:

| `type`       | Required keys                                                                     |
| ------------ | --------------------------------------------------------------------------------- |
| `odd_shape`  | `shapes` (list of kinds: `circle/square/triangle/star/heart/diamond/diamond_broken`), `shape_colors` (RGB tuples), `cols`, `answer_index`, `answer_explanation` |
| `math`       | `problem`, `choices` (4 multi-choice answers), `answer_index`, `answer_explanation` |
| `riddle`     | `instruction` (the riddle), `choices`, `answer_index`, `answer_explanation`       |

A puzzle entry looks like:

```python
{
    "id": "shape-007-example",
    "type": "odd_shape",
    "hook": "Which triangle doesn't belong?",
    "instruction": "Look closely!",
    "bg": ("#colors", "#gradient"),
    "shapes": ["triangle"] * 15 + ["star"],
    "shape_colors": [(80, 180, 255)] * 16,
    "cols": 4, "size": 130,
    "answer_index": 15,
    "answer_explanation": "A star snuck into the triangle party!",
    "cta": "FOLLOW for more puzzles!",
}
```

Puzzle ids must be unique across the bank (they're used as state keys).

### Running locally

```bash
pip install -r requirements.txt
export YOUTUBE_CLIENT_ID=... YOUTUBE_CLIENT_SECRET=... YOUTUBE_REFRESH_TOKEN=...
# Render and upload the next pending short:
python scripts/post_shorts.py

# Just render a specific puzzle (no upload), for preview:
python scripts/generate_short.py --puzzle-id shape-001-red-circles
# -> writes shorts_output/shape-001-red-circles.mp4
```

`generate_short.py` also takes `--silent` to render video-only without TTS
(useful if you're offline and can't reach the edge-tts service).

---

## Setup (one-time)

### 1. Twitch API credentials (for the VOD pipeline)

1. Go to the [Twitch Developer Console](https://dev.twitch.tv/console/apps) and
   register an application.
2. Note the **Client ID** and generate a **Client Secret**.

### 2. YouTube API credentials (shared by both pipelines)

1. In [Google Cloud Console](https://console.cloud.google.com/), create a
   project and enable the **YouTube Data API v3**.
2. Create an **OAuth client ID** (type: Desktop app). Note the client id and
   client secret.
3. Perform the OAuth flow once locally to obtain a **refresh token** with
   scopes `https://www.googleapis.com/auth/youtube.upload` and
   `https://www.googleapis.com/auth/youtube`.

### 3. GitHub repository secrets

Add these under **Settings → Secrets and variables → Actions → Secrets**:

| Secret                  | Description                                           |
| ----------------------- | ----------------------------------------------------- |
| `TWITCH_CLIENT_ID`      | Twitch application client id (VOD pipeline only)      |
| `TWITCH_CLIENT_SECRET`  | Twitch application client secret (VOD pipeline only)  |
| `TWITCH_USER_LOGIN`     | Your Twitch username (VOD pipeline only)              |
| `YOUTUBE_CLIENT_ID`     | Google OAuth client id  (both pipelines)              |
| `YOUTUBE_CLIENT_SECRET` | Google OAuth client secret (both pipelines)           |
| `YOUTUBE_REFRESH_TOKEN` | OAuth refresh token obtained locally (both pipelines) |

### 4. Optional repository variables

Add under **Settings → Secrets and variables → Actions → Variables**:

| Variable                   | Description                                            |
| -------------------------- | ------------------------------------------------------ |
| `AUTOMATION_ENABLED`       | Must be set to `true` before any workflow will run     |
| `YOUTUBE_PRIVACY`          | `private` (Twitch default), `unlisted`, or `public`.   |
|                            | The Shorts workflow defaults to `public` if unset.     |
| `YOUTUBE_PLAYLIST_ID`      | Playlist to add Twitch VOD uploads to (optional)       |
| `YOUTUBE_SHORTS_PLAYLIST_ID` | Playlist to add Shorts uploads to (optional)         |

### 5. Enable automation

Both workflows are **off by default**. Set the repository variable
`AUTOMATION_ENABLED` to `true` when you're ready for them to post.

> ⚠️ **COPPA note:** The Shorts pipeline uploads with
> `selfDeclaredMadeForKids: true` and tags every upload with
> `#forkids`/`kids video`. This treats the channel as Made for Kids per
> YouTube policy — comments will be disabled on Shorts, and YouTube will
> show contextual (not personalized) ads. If you don't want this, set the
> `MADE_FOR_KIDS` env var to `false` in `.github/workflows/kids-shorts.yml`,
> but make sure your content actually complies with COPPA before flipping
> that switch.

---

## Notes and limits

- YouTube Data API uploads cost ~1600 quota units each; the default daily
  quota (10,000) allows roughly 6 uploads/day total (VODs + Shorts combined).
  Daily scheduling for Shorts plus 4 VOD checks/day stays well under that.
- Generated videos are written to `shorts_output/` and are gitignored
  (they're ~2 MB each but rebuilding them on each Actions run is trivial).
- All video frames are rendered with Pillow; audio with edge-tts + ffmpeg.
  No external image, music, or voiceover services are required beyond the
  edge-tts WebSocket (which is free).
- Twitch VOD downloads go to `downloads/` and are cleaned up after upload.
- Never commit credentials — everything lives in GitHub Actions secrets.
