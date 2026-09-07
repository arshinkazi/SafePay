# DEPLOYMENT.md - Quick Reference

Full walkthrough lives in `README.md` (Deploy Backend to Render / Deploy
Frontend to Vercel / Connect Frontend to Backend). This file is the
condensed reference to come back to once you've deployed once and just need
the exact values.

## Architecture

```
GitHub repo
├── backend/   → Render Web Service → gunicorn app:app → SQLite (backend/safepay.db)
└── frontend/  → Vercel (static)    → talks to Render over HTTPS
```

No build step on either side. No Docker. No database service - SQLite lives
on the backend's own filesystem (see README → SQLite / Persistence for the
caveat that comes with that).

## Render configuration

| Setting | Value |
|---|---|
| Root Directory | `backend` |
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app` |
| Health Check Path | `/health` |

Environment variables to set in the Render dashboard:

| Key | Value |
|---|---|
| `JWT_SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `FRONTEND_URL` | Your Vercel URL, e.g. `https://safepay.vercel.app` |
| `RAZORPAY_KEY_ID` | Optional - omit for demo mode |
| `RAZORPAY_KEY_SECRET` | Optional - omit for demo mode |

`render.yaml` at the repo root encodes all of the above as a Blueprint, if
you'd rather not enter it by hand.

## Vercel configuration

| Setting | Value |
|---|---|
| Root Directory | `frontend` |
| Framework Preset | Other (static - no build step) |
| Build Command | (none) |
| Output Directory | `.` |

`frontend/vercel.json` already encodes "no build step" so Vercel doesn't try
to auto-detect a framework.

## The one line that connects them

`frontend/index.html`, near the top of `<head>`:

```html
window.SAFEPAY_API_URL = '';   // set to your Render URL, e.g. 'https://safepay-backend.onrender.com'
```

Order of operations that avoids a chicken-and-egg problem:
1. Deploy the backend first with `FRONTEND_URL` left blank or set to a guess
   - CORS will just fall back to `*` until you set it.
2. Deploy the frontend, note its real Vercel URL.
3. Set `FRONTEND_URL` on Render to that URL, redeploy the backend.
4. Set `window.SAFEPAY_API_URL` in `frontend/index.html` to the Render URL,
   commit, let Vercel redeploy.

## Local development

```bash
# backend
cd backend && python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit JWT_SECRET_KEY at minimum
python app.py          # http://127.0.0.1:5000

# frontend (separate terminal)
cd frontend && python3 -m http.server 8080   # http://localhost:8080
```

Local dev needs zero configuration beyond `JWT_SECRET_KEY` - the frontend's
default (`http://127.0.0.1:5000`) and the backend's default CORS allow-list
already match each other.

## Troubleshooting

See README → Troubleshooting for the full table. The two issues that come up
almost every time:

- **CORS error in the browser console** → `FRONTEND_URL` on Render doesn't
  exactly match the frontend's real origin (scheme, host, and no trailing
  slash all have to match).
- **Every request fails / stuck on "Loading…"** → `window.SAFEPAY_API_URL`
  in the deployed frontend still says `''` or `127.0.0.1`.

## Production checklist

```
[ ] JWT_SECRET_KEY set on Render (not the demo fallback)
[ ] FRONTEND_URL set on Render to the real Vercel origin
[ ] window.SAFEPAY_API_URL set in the deployed frontend to the real Render URL
[ ] /health returns 200 on the Render URL
[ ] Register → login → transfer → fraud-check all work end to end
[ ] No .env file committed to git
[ ] FLASK_DEBUG not set (or set to false) on Render
```
