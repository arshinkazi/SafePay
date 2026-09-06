# SafePay — Fraud-Protected Payments

A fintech portfolio project: a wallet app with JWT authentication, a Razorpay
top-up flow, and a 13-rule real-time fraud detection engine that scores every
transfer and can block, flag, or require OTP verification for risky ones.

---

## Overview

SafePay is a Flask + SQLite backend paired with a single-file static
frontend. Every wallet transfer is scored by a rule-based fraud engine before
it's allowed to go through — new devices, first-time recipients, unusual
amounts, odd hours, rapid-fire transactions, and unfamiliar locations all
raise the risk score, which then decides whether the transfer is approved,
flagged for review, blocked outright, or held for email OTP verification.

This repo is set up to deploy in the simplest way that works well for a
demo/portfolio project:

- **Frontend** → Vercel (static, no build step)
- **Backend** → Render (Flask via Gunicorn)
- **Database** → SQLite (see [SQLite / Persistence](#sqlite--persistence) below)

## Features

- Username/password auth with PBKDF2-SHA256 hashing and JWT sessions
- Wallet top-ups via Razorpay (or demo mode with no keys configured)
- P2P wallet transfers scored in real time by the fraud engine
- Email OTP step-up verification for high-risk transfers
- Transaction history, personal stats, and a system-wide fraud dashboard
- Device fingerprinting and per-user known-location tracking

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask, Flask-JWT-Extended, Flask-CORS |
| Server | Gunicorn (production), Flask dev server (local) |
| Database | SQLite (WAL mode) |
| Payments | Razorpay (optional — demo mode without keys) |
| Frontend | Single static HTML/CSS/JS file, no framework or build step |
| Hosting | Render (backend), Vercel (frontend) |

## Project Structure

```
safepay/
├── render.yaml                 ← Render Blueprint (optional one-click config)
├── .gitignore
├── backend/
│   ├── app.py                  ← Flask application factory + module-level `app`
│   ├── requirements.txt
│   ├── .env.example            ← copy to .env for local dev
│   ├── fraud/
│   │   └── fraud_engine.py     ← 13-rule fraud scoring engine
│   ├── models/
│   │   └── database.py         ← schema, init_db(), get_db()
│   ├── routes/
│   │   ├── auth.py             ← /register /login /me /balance /send-otp /verify-otp
│   │   ├── payments.py         ← /create-order /verify-payment /transfer /fraud-check /transactions
│   │   └── stats.py            ← /stats /fraud-stats /health
│   └── utils/
│       └── security.py         ← PBKDF2 hashing, rate limiter, device fingerprint, validation
└── frontend/
    ├── index.html               ← complete SPA (no build step required)
    └── vercel.json               ← tells Vercel this is a static site
```

---

## Local Development

### Prerequisites
- Python 3.10+
- pip

### 1. Backend

```bash
cd backend

python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# edit .env and set JWT_SECRET_KEY at minimum:
#   python3 -c "import secrets; print(secrets.token_hex(32))"

python app.py
# Server starts at http://127.0.0.1:5000
```

Without a `.env` file, the app still runs — it falls back to a built-in demo
JWT secret and logs a warning. That's fine for poking around locally, but set
a real one before you rely on it for anything.

### 2. Frontend

```bash
cd frontend
python3 -m http.server 8080
# Open http://localhost:8080
```

Or just open `frontend/index.html` directly in your browser — it defaults to
talking to `http://127.0.0.1:5000`, which matches the backend's local default
above, so no configuration is needed for local development.

---

## Environment Variables

All backend configuration is read from environment variables. See
`backend/.env.example` for the full list with explanations. Summary:

| Variable | Required? | Purpose |
|---|---|---|
| `JWT_SECRET_KEY` | **Yes, in production** | Signs JWTs. Falls back to a demo secret locally (with a warning). |
| `FRONTEND_URL` | **Yes, in production** | Your deployed frontend origin(s), comma-separated. Locks down CORS. |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | No | Omit to run wallet top-ups in demo mode. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_FROM` | No | Omit to run OTP in demo mode (code is logged + returned in the API response). |
| `DATABASE_PATH` | No | Override where the SQLite file lives. Defaults to `backend/safepay.db`. |
| `PORT` | No | Local dev server port only. Render sets this itself; Gunicorn's bind is configured on Render's side. |
| `FLASK_DEBUG` | No | Local dev only. Never set this in production. |
| `LOG_TO_FILE` | No | Set to `false` to skip the local `safepay.log` file (stdout logging is always on). |

Secrets never reach the frontend: the Razorpay **secret** stays backend-only,
and the frontend only ever receives the public `key_id`.

---

## Deploy Backend to Render

1. Push this repository to GitHub.
2. In Render, click **New +** → **Web Service** and select the repo.
   (Or use **New +** → **Blueprint** to pick up `render.yaml` automatically —
   either approach ends up with the same configuration.)
3. Configure the service:
   - **Root Directory:** `backend`
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
4. Add environment variables (Render dashboard → Environment):
   - `JWT_SECRET_KEY` — a long random value (see command above)
   - `FRONTEND_URL` — your Vercel URL (you can update this after step 2 of
     the frontend deploy below and redeploy)
   - `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` — optional
5. Deploy.
6. Once live, open `https://<your-service>.onrender.com/health` and confirm
   it returns `{"status": "ok", ...}`.

Render's free tier spins down when idle, so the first request after a period
of inactivity can take a few seconds while it wakes back up — that's normal.

## Deploy Frontend to Vercel

1. Import the GitHub repository into Vercel.
2. Set the **Root Directory** to `frontend`.
3. Framework preset: **Other** (it's a static file — no build step).
4. Deploy.
5. Open `frontend/index.html` in the deployed repo (via GitHub's web editor,
   or locally) and set the deployment config line near the top:
   ```html
   window.SAFEPAY_API_URL = 'https://your-service.onrender.com';
   ```
6. Commit and push — Vercel redeploys automatically.

## Connect Frontend to Backend

The frontend has exactly one line to change to point at a different backend —
it's flagged clearly at the top of `frontend/index.html`:

```html
window.SAFEPAY_API_URL = '';   // ← set to your Render URL for production
```

Leave it as `''` for local development (it falls back to
`http://127.0.0.1:5000`). If you ever need to point an already-deployed
frontend at a different backend without editing and redeploying, you can also
set it from the browser console:

```js
localStorage.setItem('sp_api_url', 'https://your-service.onrender.com');
```

Once you know your Vercel URL, set it as `FRONTEND_URL` on Render (step 4
above) and redeploy the backend so CORS allows requests from it.

## Health Check

```
GET /health
```

Returns `{"status": "ok", "ml_available": false, "timestamp": "..."}` and
requires no authentication. Use it to confirm the Render deployment is alive,
or as the target for Render's built-in health check (already set in
`render.yaml`).

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Frontend shows network errors on every request | `window.SAFEPAY_API_URL` still points at `127.0.0.1` — set it to your Render URL. |
| Browser console shows a CORS error | `FRONTEND_URL` on the backend doesn't match the frontend's actual deployed origin (check for a trailing slash or `http` vs `https`). |
| Render deploy fails during build | Root Directory isn't set to `backend`, so `requirements.txt` isn't found. |
| Render deploy builds but crashes on start | Check the Render logs — a missing/misspelled `app:app` in the start command is the usual cause. |
| `/health` works but login/transfer fail | JWT_SECRET_KEY may differ between what you tested locally and what's set on Render — that's expected and fine, tokens just aren't portable across the two. |
| Login works but wallet balance won't update after a top-up | Confirm `RAZORPAY_KEY_ID`/`SECRET` are both set (or both left unset for demo mode) — a mismatched pair causes signature verification to fail. |

---

## Security Notes

| Feature | Implementation |
|---|---|
| Password storage | PBKDF2-SHA256, 310,000 iterations, 32-byte random salt |
| JWT sessions | HS256, 4-hour expiry, secret loaded from `JWT_SECRET_KEY` |
| Rate limiting | Login: 10/15min per IP · Register: 5/hr per IP · Transfer: 15/hr per user · OTP send: 3/5min per user |
| SQL injection | Parameterised queries throughout — no string interpolation |
| CORS | Locked to `FRONTEND_URL` in production instead of `*` |
| Security headers | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` |
| Error responses | JSON only — no stack traces or HTML error pages leak to clients |
| Audit logging | Every login attempt recorded in the `login_attempts` table |
| Payment secrets | Razorpay secret is backend-only; frontend only ever sees the public key ID |

This is a portfolio/demo project, not an audited production fintech system.
A few things worth knowing if you take this further:

- Rate limiting is in-memory (per process) — fine for a single demo instance,
  but won't share state across multiple server processes. Redis +
  `flask-limiter` would fix that.
- OTP codes are stored in memory the same way — they don't survive a
  restart, and won't work correctly if you ever run more than one backend
  instance behind a load balancer.
- Without `SMTP_HOST` configured, OTP codes are logged and also returned in
  the API response so the demo works without real email — don't do this in
  a real product.

## SQLite / Persistence

SQLite is used for this portfolio/demo deployment because it needs no
separate database service and keeps the whole thing deployable in minutes.
Render's filesystem persistence for the free/starter tiers isn't guaranteed
across deploys or restarts, so **don't rely on data surviving indefinitely**
— treat it as demo data. If you need real persistence, the cleanest upgrade
path is to mount a Render persistent disk and point `DATABASE_PATH` at it, or
to move to a managed Postgres instance (this repo intentionally does not do
that migration, per the project's scope).

## Future Improvements

- Move rate limiting and OTP storage to Redis so they survive restarts and
  work across multiple backend instances
- Migrate to PostgreSQL if persistence or concurrent-write scale becomes a
  real requirement
- Add automated tests and a CI workflow
- Re-introduce a real ML scoring layer (the current engine is intentionally
  rule-based — see `fraud/fraud_engine.py`'s module docstring for why)

---

## API Reference

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/health` | — | Health check |
| POST | `/register` | — | Create account |
| POST | `/login` | — | Get JWT token |
| GET | `/me` | JWT | Current user profile |
| GET | `/balance` | JWT | Current wallet balance |
| POST | `/send-otp` | JWT | Send a fresh OTP for a high-risk transfer |
| POST | `/verify-otp` | JWT | Verify an OTP code |
| POST | `/create-order` | JWT | Create a Razorpay order (or demo order) |
| POST | `/verify-payment` | JWT | Verify signature + credit wallet |
| POST | `/transfer` | JWT | P2P wallet transfer (fraud-checked) |
| POST | `/fraud-check` | JWT | Dry-run fraud score (no commit) |
| GET | `/transactions` | JWT | Transaction history |
| GET | `/stats` | JWT | Personal account statistics |
| GET | `/fraud-stats` | JWT | System-wide fraud analytics |

### Example: Transfer

```bash
curl -X POST https://your-service.onrender.com/transfer \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 1500,
    "recipient": "alice",
    "DeviceInfo": "Chrome 121",
    "lat": 26.9124,
    "lon": 75.7873
  }'
```

---

## Fraud Detection — How It Works

All scoring is rule-based (13 signals, additive, capped at 100) — no ML model
ships with this repo. `ML_AVAILABLE` is reported as `false` via `/health` and
`/fraud-stats` for exactly this reason.

| Rule | Score Boost | Trigger |
|------|:-----------:|---------|
| New device | +15 | Device fingerprint never seen for this user |
| First-time recipient | +14 | Never sent to this recipient before |
| Round-amount structuring | +10 | Round multiple of ₹1,000, ≥ ₹5,000 |
| Unusual amount | +20 | > 4× the user's 30-day average |
| Large amount (₹50K–79K) | +14 | — |
| Large amount (₹80K+) | +22 | — |
| High velocity | +24 | ≥4 transactions in the last hour |
| Rapid fire | +22 | ≥2 transactions in the last 5 minutes |
| Recent failures | +16 | ≥2 blocked/failed in the last 24 hours |
| Late night | +14 | 23:00–04:59 UTC |
| Unusual location | +32 | >50 km from any known location for this user |
| Dormant account | +18 | No successful transaction in ≥21 days |
| Multi-recipient fan-out | +12 | ≥3 distinct recipients today |
| Escalating amounts | +16 | Last 3 transfers strictly increasing, top > 3× bottom |

| Score | Level | Action |
|-------|-------|--------|
| < 30 | LOW | Approve normally |
| 30–54 | MEDIUM | Approve, logged as "suspicious" |
| 55–74 | HIGH | Held for email OTP verification |
| ≥ 75 | HIGH (block) | Rejected outright, no bypass |

---

## Razorpay Integration

```
User → POST /create-order {amount}
     ← {order_id, key_id, amount, currency}

User opens Razorpay checkout with order_id
User pays → Razorpay success handler fires

User → POST /verify-payment {razorpay_order_id, razorpay_payment_id, razorpay_signature}
     ← {status: "success", new_balance}
```

Signature verification uses HMAC-SHA256 against `RAZORPAY_KEY_SECRET`. If no
Razorpay keys are configured, `/create-order` returns a demo order and
signature verification is skipped for that order — the wallet still credits
correctly so the whole flow works without real payment credentials.

**Razorpay test card**: `4111 1111 1111 1111`, any future expiry, CVV `123`.

---

## Database Schema

```sql
users           (id, username, email, password, balance, created_at, last_login)
transactions    (id, transaction_id, username, recipient, amount, status,
                 payment_method, risk_score, risk_level, risk_flags,
                 ml_score, rule_score, device_hash, device_info,
                 location_lat, location_lon, ip_address,
                 razorpay_order_id, razorpay_payment_id, created_at)
known_devices   (id, username, device_hash, first_seen, last_seen)
login_attempts  (id, username, ip_address, success, created_at)
razorpay_orders (id, order_id, username, amount, amount_paise,
                 status, payment_id, created_at)
known_locations (id, username, lat, lon, label, visit_count, first_seen, last_seen)
```

---

## Deployment Checklist

```
[ ] Push code to GitHub
[ ] Create Render backend (Root Directory: backend, Build: pip install -r requirements.txt, Start: gunicorn app:app)
[ ] Add JWT_SECRET_KEY and FRONTEND_URL environment variables on Render
[ ] Deploy backend
[ ] Verify /health returns {"status": "ok"}
[ ] Copy the Render backend URL
[ ] Deploy frontend to Vercel (Root Directory: frontend)
[ ] Set window.SAFEPAY_API_URL in frontend/index.html to the Render URL
[ ] Set FRONTEND_URL on Render to the actual Vercel URL, redeploy backend
[ ] Test register + login
[ ] Test a wallet top-up (demo mode is fine)
[ ] Test a transfer and confirm fraud scoring runs
[ ] Test the fraud dashboard
[ ] Check the browser console for errors
[ ] Confirm no .env file or real secrets were committed
```
