# Deployment notes

## Backend: one worker

The backend runs two background tasks inside the API process: the auto-sync scheduler
(`backend/app/connectors/scheduler.py`) and the voice note transcription worker (`backend/app/ai/speech.py`).
They start when the app starts, unless `AUTO_SYNC_ENABLED=false` / `TRANSCRIPTION_WORKER_ENABLED=false`.

- Run **one** backend worker: `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (no `--workers N`, no
  `gunicorn -w N`), and keep the Render/Railway service at **one instance**.
- Each worker would start its own scheduler. The per-source lock stops two workers from syncing the same source
  at the same time, but the run times and the "new messages" counts would be wrong.
- If you need more than one worker, set `AUTO_SYNC_ENABLED=false`. The Sync button still works, and the Sources
  card says that auto-sync is off on this server.
- The transcription worker has the same rule: with several workers, set `TRANSCRIPTION_WORKER_ENABLED=false` on
  all but one (or everywhere, and voice notes wait as "Waiting to be transcribed"). A per-message claim stops two
  workers from transcribing the same note, but Groq's 20 requests/minute limit is counted per process.

## Auto-sync behaviour

- Every API source (Jira, Slack, Gmail, Telegram, Discord) has Auto-sync on/off and an interval of 1, 5, 15 or
  60 minutes (default 5). Telegram starts with auto-sync **on**, because Telegram keeps updates for about
  24 hours only.
- Only one sync per source runs at a time. A run that finds the previous one still going is skipped.
- Sources are spread over their interval, so they do not all start at once.
- After 3 failed syncs in a row, auto-sync pauses for that source ("Paused after errors – Retry"). A successful
  sync, or turning auto-sync on again, ends the pause.
- Gmail: while the Google OAuth client is in "Testing" mode, Google expires refresh tokens after 7 days. Auto-sync
  then stops for that source and the card shows "Reconnect Google account". Connecting the account again
  restarts auto-sync.
- With the project's "Auto-extract" setting on, requirements are extracted and validated from the newly synced
  messages only, right after their sync. They remain AI suggestions for the team lead to review.
- The free Render plan sleeps after inactivity. A sleeping backend does not sync. Telegram updates older than
  about 24 hours are lost, so open the app at least once a day, or use a plan that does not sleep.

## Voice notes

Voice notes from every source are transcribed with Groq Whisper (`GROQ_API_KEY`), one by one in the background.

- Sources: Telegram voice/audio, Discord voice messages and audio attachments, Slack audio clips and audio uploads,
  Gmail and Jira audio attachments, WhatsApp ZIP exports "with media", Messenger export ZIPs, .eml audio
  attachments, and audio files uploaded on the Sources page (mp3, m4a, ogg/opus, wav, webm, max 25 MB).
- Models: always `whisper-large-v3` (better for Bangla); `whisper-large-v3-turbo` only when large-v3 is busy,
  rate limited or times out. The project's "Voice language" (Auto / Bangla / English) is sent to Whisper.
- Auto mode: Whisper often hears Bangla as Hindi and writes Devanagari. When the detected language is not in
  `VOICE_ALLOWED_LANGUAGES` (default `bn,en`) or the text contains Devanagari, the note is transcribed once more as
  Bangla. That costs a second Groq request. For a project whose notes are nearly all Bangla, set Voice language to
  Bangla. Garbled English that Whisper reports as English is not caught; use "Re-transcribe as".
- "Re-transcribe as: Bangla / English" on the Messages page needs the audio: it works when `KEEP_AUDIO=true`, or
  for API sources (Telegram, Slack, ...) whose connector downloads it again. With `KEEP_AUDIO=false`, uploaded
  voice notes cannot be re-transcribed after their first transcript.
- Groq limits (free tier): 20 requests per minute (`GROQ_AUDIO_RPM`) and a number of audio seconds per hour and per
  day. A rate-limited note goes back to the queue and is tried later; after 5 failed attempts it shows
  "voice (not transcribed)" with a Retry button. Notes over 25 MB are kept but not transcribed.
- Slack needs the `files:read` bot scope for voice notes: Slack app → OAuth & Permissions → Bot Token Scopes →
  add `files:read`, then reinstall the app to the workspace. Until then the Slack card shows "Add files:read to
  read voice notes" and Slack voice notes wait with a Retry button.
- Telegram bots can only download files up to 20 MB.
- `KEEP_AUDIO=false` (default) deletes the audio once it is transcribed. `KEEP_AUDIO=true` keeps it in the
  `voice_audio` table so the Messages page can play it. Audio is stored in the database: the Supabase free tier has
  500 MB, so keep this off unless you need playback. Audio that waits for transcription, or whose transcription
  failed, is always kept until it is transcribed.
- **Upload size on Vercel:** uploads go through the Next.js route `/api/backend/...`, and Vercel limits a request
  body to 4.5 MB. Larger voice notes and ZIP exports work locally and with the frontend on Render/Railway, but
  fail on Vercel. Synced voice notes (Telegram, Slack, ...) are not affected; they never pass through the frontend.
