# Deployment notes

## Backend: one worker

The backend runs the auto-sync scheduler (`backend/app/connectors/scheduler.py`) as a background task inside
the API process. It starts when the app starts, unless `AUTO_SYNC_ENABLED=false`.

- Run **one** backend worker: `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (no `--workers N`, no
  `gunicorn -w N`), and keep the Render/Railway service at **one instance**.
- Each worker would start its own scheduler. The per-source lock stops two workers from syncing the same source
  at the same time, but the run times and the "new messages" counts would be wrong.
- If you need more than one worker, set `AUTO_SYNC_ENABLED=false`. The Sync button still works, and the Sources
  card says that auto-sync is off on this server.

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
