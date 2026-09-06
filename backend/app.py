"""
app.py — SafePay Flask Application Entry Point
===============================================
Application factory pattern keeps this file lean.
All logic lives in route blueprints and utility modules.

Local development:
  cd safepay/backend
  pip install -r requirements.txt
  export JWT_SECRET_KEY="your-32-char-secret-here"
  python app.py
  # Server starts at http://127.0.0.1:5000

Production (Render, or any WSGI host):
  gunicorn app:app
  # This module exposes a module-level `app` object for Gunicorn to import.
"""

import os
import sys
import logging
import datetime

from flask import Flask, jsonify
from flask_jwt_extended import JWTManager
from flask_cors import CORS

# ── Logging setup ─────────────────────────────────────────────────────────────
# Always log to stdout — this is what Render (and most cloud platforms) capture
# automatically. The file handler is best-effort only: some hosts run on a
# read-only or ephemeral filesystem, and deployment must not depend on the file
# existing, so a failure to open it is logged and swallowed rather than crashing
# the app.
_log_handlers = [logging.StreamHandler(sys.stdout)]
if os.environ.get("LOG_TO_FILE", "true").lower() not in ("0", "false", "no"):
    try:
        _log_handlers.append(logging.FileHandler("safepay.log", encoding="utf-8"))
    except OSError:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=_log_handlers,
)
log = logging.getLogger("safepay")


_DEMO_JWT_SECRET = "safepay-demo-secret-key-change-in-production-at-least-32chars"

# Local-dev origins that are always allowed so `python app.py` + a static
# frontend server keeps working out of the box with no extra configuration.
_DEFAULT_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8080",
    "http://127.0.0.1:5000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8080",
]


def _cors_origins() -> list[str] | str:
    """
    Build the CORS allow-list from FRONTEND_URL (comma-separated for multiple
    domains, e.g. a Vercel production URL + a Vercel preview URL). Local dev
    origins are always included so nothing extra needs to be set to develop
    locally. Falls back to "*" (without credentials) only if nothing is
    configured, so the app still works before FRONTEND_URL is set.
    """
    configured = os.environ.get("FRONTEND_URL", "").strip()
    if not configured:
        return "*"
    origins = [o.strip() for o in configured.split(",") if o.strip()]
    return origins + _DEFAULT_DEV_ORIGINS


def create_app() -> Flask:
    """Application factory — creates and configures the Flask app."""
    app = Flask(__name__)

    # ── JWT configuration ─────────────────────────────────────────────────────
    jwt_secret = os.environ.get("JWT_SECRET_KEY", _DEMO_JWT_SECRET)
    if jwt_secret == _DEMO_JWT_SECRET:
        log.warning(
            "⚠️  JWT_SECRET_KEY is not set — using the built-in demo secret. "
            "This is fine for local development but MUST be set to a random "
            "value before deploying. Generate one with: "
            "python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )
    app.config["JWT_SECRET_KEY"] = jwt_secret
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = datetime.timedelta(hours=4)

    jwt = JWTManager(app)

    # ── JWT error handlers ────────────────────────────────────────────────────
    @jwt.unauthorized_loader
    def missing_token_cb(err):
        return jsonify({"msg": "Missing or invalid Authorization header"}), 401

    @jwt.invalid_token_loader
    def invalid_token_cb(err):
        return jsonify({"msg": "Invalid token"}), 401

    @jwt.expired_token_loader
    def expired_token_cb(jwt_header, jwt_payload):
        return jsonify({"msg": "Token has expired — please log in again"}), 401

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Set FRONTEND_URL (e.g. https://your-app.vercel.app) in production so the
    # allow-list is locked to your real domain instead of "*". See .env.example.
    cors_origins = _cors_origins()
    CORS(
        app,
        resources={r"/*": {"origins": cors_origins}},
        supports_credentials=(cors_origins != "*"),
    )

    # ── Security headers ──────────────────────────────────────────────────────
    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"]        = "DENY"
        response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
        return response

    # ── JSON error handlers ───────────────────────────────────────────────────
    # Make sure production never leaks a Python traceback or HTML error page —
    # every error response stays JSON, matching the rest of the API.
    @app.errorhandler(404)
    def not_found(err):
        return jsonify({"msg": "Not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(err):
        return jsonify({"msg": "Method not allowed"}), 405

    @app.errorhandler(500)
    def internal_error(err):
        log.error("Unhandled server error: %s", err, exc_info=True)
        return jsonify({"msg": "Internal server error"}), 500

    # ── Database ──────────────────────────────────────────────────────────────
    from models.database import init_db, close_db
    init_db()
    app.teardown_appcontext(close_db)

    # ── Register blueprints ───────────────────────────────────────────────────
    from routes.auth     import auth_bp
    from routes.payments import payments_bp
    from routes.stats    import stats_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(stats_bp)

    # ── OTP routes (already on auth_bp via send-otp / verify-otp) ───────────
    log.info("✅ SafePay app created — routes registered (incl. OTP email verification)")
    return app


# ── Module-level app object ───────────────────────────────────────────────────
# Gunicorn (and any other WSGI server) imports this directly: `gunicorn app:app`.
# Building it at import time is what makes that command work in production.
app = create_app()

# ── Dev entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Only for local development — Render/production runs via Gunicorn instead
    # (see the Procfile / render.yaml start command), so this code path is
    # never used in production and it's safe for debug mode to default off.
    host  = os.environ.get("HOST", "127.0.0.1")
    port  = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
    log.info("🚀 SafePay backend starting on http://%s:%s (debug=%s)", host, port, debug)
    app.run(host=host, port=port, debug=debug)
