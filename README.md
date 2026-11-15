# Mirsal Messenger

Arabic RTL messaging prototype with profiles, PostgreSQL/SQLite persistence, Twilio Verify phone login, and WebRTC peer-call signaling.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn src.app:app --reload
```

Open http://localhost:8000. Without `DATABASE_URL`, local development uses `mirsal.db` SQLite. Set `DATABASE_URL` to a PostgreSQL connection string to use PostgreSQL. `postgres://` and `postgresql://` URLs are normalized to psycopg 3.

## Phone verification

Configure a Twilio Verify service and set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_VERIFY_SERVICE_SID` in the server environment. Credentials must not be added to frontend code or committed. Without these values, registration correctly fails closed rather than returning a fake OTP.

## WebRTC calls

The API provides authenticated WebSocket signaling at `/ws/calls/{call_id}`. Browsers exchange SDP offers/answers and ICE candidates through the app; media flows peer-to-peer. Configure `TURN_URLS`, `TURN_USERNAME`, and `TURN_CREDENTIAL` for a production TURN provider to connect peers behind restrictive NAT/firewalls. STUN-only calls are best-effort and are not reliably connectable on all networks. Use HTTPS/WSS (Render supplies HTTPS). Live broadcast to many viewers still needs an SFU/media server; this WebRTC signaling implements one-to-one calls.

## Deploy to Render

`render.yaml` defines the FastAPI web service and managed PostgreSQL database. In Render, choose **New → Blueprint**, select this repository and the `feature/mirsal-messaging-app` branch, then apply the blueprint. Fill in the Twilio and TURN environment variables in the service dashboard. Keep secrets in Render's environment settings. The deployment was not launched from this repository because it requires access to your Render account and provider credentials.

The database schema is created automatically at startup; for a production app, introduce Alembic migrations before evolving schema further. Configure backups, rate limits and data-retention/privacy policies before handling real user data.
