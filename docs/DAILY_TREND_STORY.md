# Daily Trend-to-Story Pipeline

Runs **every day at 7:00 AM Eastern Time** (America/New_York), DST-safe, and
publishes one ~2:30 narrated story video to your YouTube channel based on what
the world was searching for the day before.

| Step | What happens | Tool |
|------|--------------|------|
| 1 | Fetch Google Daily Trends (yesterday's top 15 US searches) | `pytrends` |
| 2 | Save top 5 words to a spreadsheet (`trends_YYYY-MM-DD.csv`) | built-in |
| 3 | Predict the topic of interest from the trending words | built-in |
| 4 | Write a ~350–450 word short story about the topic | OpenAI (or template fallback) |
| 5 | Narrate the story (target 2:30–2:40) | Microsoft Edge TTS (free) |
| 6 | Source 8 stock images for the visuals | Pexels (free) |
| 7 | Assemble a 1920×1080 MP4 (Ken Burns zoom + narration) | ffmpeg |
| 8 | Validate the video (duration / corruption check) | ffprobe |
| 9 | Upload to YouTube with thumbnail, description, and tags | YouTube Data API v3 |

---

## How the 7:00 AM Eastern schedule works (DST-safe)

GitHub Actions cron is always evaluated in **UTC**, so the workflow uses two
cron entries:

```yaml
- cron: "0 11 * * *"   # 11:00 UTC = 7:00 AM EDT (summer)
- cron: "0 12 * * *"   # 12:00 UTC = 7:00 AM EST (winter)
```

Exactly one of those two times falls inside the script's run window
(**06:30–07:45 Eastern**), so only one run per day proceeds — summer or winter,
transition Sundays included. The state file (`state/trend_videos.json`) is a
second guard against double uploads: each date is processed once.

Manual triggers (`workflow_dispatch`) pass `--force` and bypass the window.

---

## Setup checklist

### 1. Add repository secrets

**Settings → Secrets and variables → Actions → Secrets:**

| Secret | Required? | Where to get it |
|--------|-----------|-----------------|
| `OPENAI_API_KEY` | No (template fallback used) | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `PEXELS_API_KEY` | No (placeholder images used) | [pexels.com/api](https://www.pexels.com/api/) (free) |
| `YOUTUBE_CLIENT_ID` | **Yes** (to upload) | Google Cloud Console → *APIs & Services → Credentials* (OAuth 2.0 Client ID, type Desktop) |
| `YOUTUBE_CLIENT_SECRET` | **Yes** | Same OAuth client as above |
| `YOUTUBE_REFRESH_TOKEN` | **Yes** | OAuth flow, see below |

### 2. (Optional) Add repository variables

**Settings → Secrets and variables → Actions → Variables:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `YOUTUBE_PRIVACY` | `private` | `private`, `unlisted`, or `public` |
| `USE_FALLBACK` | `0` | Set `1` to skip OpenAI entirely (template stories only) |
| `YOUTUBE_SCOPE` | `https://www.googleapis.com/auth/youtube.upload` | Comma-separated OAuth scopes (e.g. add `youtube.force-ssl` if your refresh token was issued with it) |

### 3. Get the YouTube refresh token

The refresh token must be issued with the upload scope. Run once locally:

```bash
pip install google-auth-oauthlib google-api-python-client
python - <<'EOF'
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json",
    scopes=["https://www.googleapis.com/auth/youtube.upload"],
)
creds = flow.run_local_server(port=0)
print("REFRESH_TOKEN:", creds.refresh_token)
EOF
```

Paste the printed token into the `YOUTUBE_REFRESH_TOKEN` secret. Rotate it
periodically by deleting the secret, re-running the flow, and re-adding it.

> **Scope note:** `youtube.upload` is the least-privilege scope and covers both
> `videos.insert` (upload) and `thumbnails.set` (thumbnail). If you prefer,
> `youtube.force-ssl` works too — just make sure the refresh token was issued
> with whatever scope you put in `YOUTUBE_SCOPE`.

### 4. Enable and test the workflow

1. Commit and push the workflow + script (the push needs a token with
   **workflows** permission — see below).
2. Open **Actions → Daily Trend Story → Run workflow** (top-right) to trigger
   a manual run before trusting the schedule.
3. Check the run log for `Upload OK! https://youtu.be/<id>`.
4. Videos default to **private** so you can review them before going public.

---

## Behavior notes & limits

- **Fallbacks keep the pipeline alive:** no `OPENAI_API_KEY` → template story;
  no `PEXELS_API_KEY` or Pexels down → placeholder gradient images; edge-tts
  unreachable → silent audio (video still assembles, but you should not publish
  a silent clip — check the artifact).
- **Retries:** pytrends, Pexels, and image downloads retry with exponential
  backoff (3–4 attempts) before falling back, so transient 429/5xx errors
  don't kill the run.
- **Quota:** each YouTube upload costs ~1600 quota units; the default 10,000
  daily quota allows ~6 uploads/day. This pipeline posts 1/day, leaving headroom.
- **Artifacts:** every run uploads the generated media (video, thumbnail, story,
  CSV, narration) as a GitHub Actions artifact with 14-day retention. Nothing
  big is committed to Git — only the small `state/trend_videos.json`.
- **Time window:** the script skips runs outside 06:30–07:45 Eastern unless
  `--force` is passed. `TZ=America/New_York` is set in the workflow so `date`
  and `datetime.now()` agree.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Missing YouTube credentials ... skipping upload` | Add the three `YOUTUBE_*` secrets and re-run |
| `refusing to allow a GitHub App to create or update workflow` | Push with a PAT that has the `workflow` scope (see below) |
| No video uploaded, log says "outside the run window" | Expected for the non-7am cron; verify with `workflow_dispatch` |
| edge-tts connection error in CI | Outbound access to `speech.platform.bing.com` required; on GitHub-hosted runners this works |
| Story is the same every day | `OPENAI_API_KEY` missing → template fallback; add the key |

## Pushing with a token that has `workflows` permission

The scheduled-run GitHub App token can't create/update workflow files. Use a
[Personal Access Token](https://github.com/settings/tokens) with **repo** and
**workflow** scopes:

```bash
git remote set-url origin https://<USERNAME>:<TOKEN>@github.com/trudts77769-byte/Twitch-to-youtube.git
git push origin arena/01a010b5-twitch-to-youtube
```

After the first push, revert to a token without the password embedded:

```bash
git remote set-url origin https://github.com/trudts77769-byte/Twitch-to-youtube.git
```
