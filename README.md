# Mirsal Messenger

Arabic RTL messaging-app prototype with SQLite persistence, phone registration through Twilio Verify, searchable conversations, live-broadcast preview, and a call-control UI.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn src.app:app --reload
```

Open http://localhost:8000. SQLite data is stored in `mirsal.db` (override with `MIRSAL_DB_PATH`). The database is created automatically.

## Enable real phone verification

Create a Twilio Verify service and set these environment variables on the server (never put credentials in browser code or commit them):

```bash
export TWILIO_ACCOUNT_SID="AC..."
export TWILIO_AUTH_TOKEN="..."
export TWILIO_VERIFY_SERVICE_SID="VA..."
```

On Windows PowerShell use `$env:TWILIO_ACCOUNT_SID="..."` and equivalent commands. Without these values, registration returns HTTP 503; no fake OTP is issued. Configure the provider's allowed destinations and billing as required.

## Current scope and security notes

- Users, sessions, conversations, messages, live-session metadata, and call records persist in SQLite. API endpoints require a bearer session token after SMS verification.
- Calls currently provide local microphone/camera preview, mute, screen-share, and end-call controls. They do **not** connect another participant; real two-way calls and public live video require WebRTC signaling plus an SFU/media server. Use HTTPS for browser camera/microphone/screen permissions outside localhost.
- Live broadcast metadata is persisted; the preview is local and is not distributed to viewers.
- For production deployment, use HTTPS, rate-limit verification endpoints, add retention/privacy controls and backups, and consider PostgreSQL and a managed secrets store.
