# Twitch to YouTube

Automatically re-uploads your Twitch VODs (past broadcasts) to YouTube on a
schedule using GitHub Actions.

## How it works

1. A scheduled GitHub Actions workflow (`.github/workflows/twitch-to-youtube.yml`)
   runs every 6 hours.
2. `scripts/sync_vods.py` asks the Twitch Helix API for your recent VODs
   (type `archive`).
3. Any VOD not yet listed in `state/uploaded_vods.json` is downloaded with
   `yt-dlp` and uploaded to YouTube via the YouTube Data API v3.
4. The VOD id is recorded in `state/uploaded_vods.json` and committed back to
   the repository so it is never uploaded twice.

## Setup

### 1. Twitch API credentials

1. Go to the [Twitch Developer Console](https://dev.twitch.tv/console/apps) and
   register an application.
2. Note the **Client ID** and generate a **Client Secret**.

### 2. YouTube API credentials

1. In [Google Cloud Console](https://console.cloud.google.com/), create a
   project and enable the **YouTube Data API v3**.
2. Create an **OAuth client ID** (type: Desktop app). Note the client id and
   client secret.
3. Perform the OAuth flow once locally to obtain a **refresh token** with the
   scopes `https://www.googleapis.com/auth/youtube.upload` and
   `https://www.googleapis.com/auth/youtube`.

### 3. GitHub repository secrets

Add these under **Settings -> Secrets and variables -> Actions -> Secrets**:

| Secret                  | Description                          |
| ----------------------- | ------------------------------------ |
| `TWITCH_CLIENT_ID`      | Twitch application client id         |
| `TWITCH_CLIENT_SECRET`  | Twitch application client secret     |
| `TWITCH_USER_LOGIN`     | Your Twitch username (login name)    |
| `YOUTUBE_CLIENT_ID`     | Google OAuth client id               |
| `YOUTUBE_CLIENT_SECRET` | Google OAuth client secret           |
| `YOUTUBE_REFRESH_TOKEN` | OAuth refresh token obtained locally |

### 4. Optional repository variables

Add these under **Settings -> Secrets and variables -> Actions -> Variables**:

| Variable              | Description                                            |
| --------------------- | ------------------------------------------------------ |
| `AUTOMATION_ENABLED`  | Must be set to `true` before the workflow will run     |
| `YOUTUBE_PRIVACY`     | `private` (default), `unlisted`, or `public`           |
| `YOUTUBE_PLAYLIST_ID` | Playlist to add uploads to (optional)                  |

### 5. Enable the automation

The workflow is **off by default**. It only runs when the repository variable
`AUTOMATION_ENABLED` is set to `true`. Set it when you are ready.

## Running locally

Requires Python >= 3.10 and `ffmpeg` on your PATH.

```bash
pip install -r requirements.txt
export TWITCH_CLIENT_ID=... TWITCH_CLIENT_SECRET=... TWITCH_USER_LOGIN=...
export YOUTUBE_CLIENT_ID=... YOUTUBE_CLIENT_SECRET=... YOUTUBE_REFRESH_TOKEN=...
python scripts/sync_vods.py
```

## Notes and limits

- YouTube API uploads cost ~1600 quota units each; the default daily quota
  (10,000 units) allows roughly 6 uploads per day.
- Uploads default to **private** so you can review them before publishing.
- Downloaded video files are deleted after each upload to save disk space and
  are ignored by Git (`downloads/` in `.gitignore`).
- Never commit credentials; all secrets live in GitHub Actions secrets.
