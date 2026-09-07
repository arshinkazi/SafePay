"""
auth.py - Authentication routes
================================
POST /register   - create new account
POST /login      - obtain JWT
GET  /me         - current user profile
GET  /balance    - current wallet balance
"""

import logging
import sqlite3

from flask import Blueprint, request, jsonify
from flask_jwt_extended import (
    create_access_token, jwt_required, get_jwt_identity
)

from models.database import get_db
from utils.security  import (
    hash_password, verify_password,
    rate_limit, validate_registration
)

log = logging.getLogger("safepay.auth")
auth_bp = Blueprint("auth", __name__)


# ── /register ──────────────────────────────────────────────────────────────────
@auth_bp.route("/register", methods=["POST"])
def register():
    """
    Create a new user account.
    Rate limited: 5 attempts per IP per hour to prevent account spam.
    Passwords are hashed with PBKDF2-SHA256 before storage.
    """
    ip = request.remote_addr
    if not rate_limit(f"reg:{ip}", 5, 3600):
        return jsonify({"msg": "Too many registration attempts. Try again later."}), 429

    data = request.get_json(silent=True) or {}

    # Validate all fields at once
    err = validate_registration(data)
    if err:
        return jsonify({"msg": err}), 400

    username = data["username"].strip().lower()
    email    = data["email"].strip().lower()
    password = data["password"].strip()

    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
            (username, email, hash_password(password))
        )
        db.commit()
        log.info("New user registered: %s from %s", username, ip)
        return jsonify({"msg": "Account created successfully!"}), 201
    except sqlite3.IntegrityError:
        return jsonify({"msg": "Username or email already exists"}), 409


# ── /login ─────────────────────────────────────────────────────────────────────
@auth_bp.route("/login", methods=["POST"])
def login():
    """
    Authenticate user and return a JWT access token.
    Rate limited: 10 attempts per IP per 15 minutes.
    Every attempt (success or failure) is logged for audit.
    """
    ip = request.remote_addr
    if not rate_limit(f"login:{ip}", 10, 900):
        return jsonify({"msg": "Too many login attempts. Try again in 15 minutes."}), 429

    data     = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return jsonify({"msg": "Username and password are required"}), 400

    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    ok   = bool(user and verify_password(password, user["password"]))

    # Always log the attempt (for security auditing)
    db.execute(
        "INSERT INTO login_attempts (username, ip_address, success) VALUES (?, ?, ?)",
        (username, ip, 1 if ok else 0)
    )

    if ok:
        db.execute(
            "UPDATE users SET last_login = datetime('now') WHERE username = ?",
            (username,)
        )
        db.commit()
        log.info("Login SUCCESS: %s from %s", username, ip)
        return jsonify({
            "access_token": create_access_token(identity=username),
            "username":     username,
            "balance":      user["balance"],
            "email":        user["email"],
        }), 200

    db.commit()
    log.warning("Login FAILED: %s from %s", username, ip)
    return jsonify({"msg": "Invalid username or password"}), 401


# ── /me ────────────────────────────────────────────────────────────────────────
@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    """Return the current user's profile (requires valid JWT)."""
    username = get_jwt_identity()
    db       = get_db()
    user     = db.execute(
        "SELECT username, email, balance, created_at, last_login FROM users WHERE username = ?",
        (username,)
    ).fetchone()

    if not user:
        return jsonify({"msg": "User not found"}), 404

    return jsonify({
        "username":   user["username"],
        "email":      user["email"],
        "balance":    round(user["balance"], 2),
        "created_at": user["created_at"],
        "last_login": user["last_login"],
    }), 200


# ── /balance ───────────────────────────────────────────────────────────────────
@auth_bp.route("/balance", methods=["GET"])
@jwt_required()
def balance():
    """Return the current wallet balance.  Always reads live from DB."""
    username = get_jwt_identity()
    db       = get_db()
    row      = db.execute(
        "SELECT balance FROM users WHERE username = ?", (username,)
    ).fetchone()

    if not row:
        return jsonify({"msg": "User not found"}), 404

    return jsonify({"balance": round(row["balance"], 2)}), 200


# ── OTP endpoints ──────────────────────────────────────────────────────────────
import random
import time
from flask_jwt_extended import jwt_required, get_jwt_identity

# In-memory OTP store: {username: {otp, expires_at, attempts}}
# In production use Redis or DB; this survives the process lifetime for demo.
_otp_store: dict = {}
OTP_TTL_SECONDS   = 300   # 5 minutes
OTP_MAX_ATTEMPTS  = 5


def _generate_otp() -> str:
    return str(random.randint(100000, 999999))


def _send_otp_email(to_email: str, otp: str, username: str) -> bool:
    """
    Send OTP email.  Uses SMTP if SMTP_HOST env var is configured,
    otherwise logs the OTP and returns True (demo mode).
    """
    import os, smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    from_addr = os.environ.get("SMTP_FROM", smtp_user or "noreply@safepay.demo")

    subject = "SafePay – Transaction Verification Code"
    body_html = f"""
<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:32px;
            border:1px solid #e5e7eb;border-radius:12px;background:#fff">
  <div style="text-align:center;margin-bottom:24px">
    <div style="width:48px;height:48px;background:#3D7BFF;border-radius:12px;
                display:inline-flex;align-items:center;justify-content:center">
      <span style="color:#fff;display:inline-flex">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
      </span>
    </div>
    <h2 style="margin:12px 0 4px;color:#111">Transaction Verification</h2>
    <p style="color:#6b7280;font-size:14px;margin:0">SafePay Security Alert</p>
  </div>

  <p style="color:#374151;font-size:15px">Hi <strong>{username}</strong>,</p>
  <p style="color:#374151;font-size:15px">
    We detected a <strong>high-risk transaction</strong> on your SafePay account.
    Use the code below to verify and proceed.
  </p>

  <div style="background:#f3f4f6;border-radius:10px;padding:24px;text-align:center;margin:24px 0">
    <p style="margin:0 0 6px;font-size:13px;color:#6b7280;text-transform:uppercase;
               letter-spacing:.08em">Your verification code</p>
    <div style="font-size:40px;font-weight:800;letter-spacing:.25em;color:#111;font-family:monospace">
      {otp}
    </div>
    <p style="margin:10px 0 0;font-size:12px;color:#9ca3af">Expires in 5 minutes</p>
  </div>

  <p style="color:#374151;font-size:14px">
    If you did not initiate this transaction, please
    <strong>do not share this code</strong> and contact SafePay support immediately.
  </p>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0">
  <p style="color:#9ca3af;font-size:12px;text-align:center">
    SafePay · Secure Payment Platform · This is an automated message.
  </p>
</div>
"""

    if not smtp_host:
        log.info("DEMO OTP for %s <%s>: %s  (set SMTP_HOST to send real email)", username, to_email, otp)
        return True   # demo mode - OTP shown in backend log / returned to frontend

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"SafePay <{from_addr}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
            s.ehlo()
            s.starttls()
            if smtp_user:
                s.login(smtp_user, smtp_pass)
            s.sendmail(from_addr, [to_email], msg.as_string())
        log.info("OTP email sent to %s for user %s", to_email, username)
        return True
    except Exception as exc:
        log.error("Failed to send OTP email to %s: %s", to_email, exc)
        return False


@auth_bp.route("/send-otp", methods=["POST"])
@jwt_required()
def send_otp():
    """
    Generate a fresh OTP and send it to the user's registered email.
    Rate-limited: max 3 sends per 5 minutes per user.
    """
    import os
    username = get_jwt_identity()
    ip       = request.remote_addr

    if not rate_limit(f"otp_send:{username}", 3, 300):
        return jsonify({"msg": "Too many OTP requests. Please wait a few minutes."}), 429

    db   = get_db()
    user = db.execute("SELECT email FROM users WHERE username = ?", (username,)).fetchone()
    if not user:
        return jsonify({"msg": "User not found"}), 404

    email = user["email"]
    otp   = _generate_otp()
    _otp_store[username] = {
        "otp":        otp,
        "expires_at": time.time() + OTP_TTL_SECONDS,
        "attempts":   0,
    }

    demo_mode = not os.environ.get("SMTP_HOST")
    sent = _send_otp_email(email, otp, username)

    if not sent:
        return jsonify({"msg": "Failed to send verification email. Please try again."}), 500

    # Mask email: sh***@gmail.com
    parts    = email.split("@")
    masked   = parts[0][:2] + "***@" + parts[1] if len(parts) == 2 else "***"

    resp = {"msg": f"Verification code sent to {masked}", "masked_email": masked}
    if demo_mode:
        # Surface the OTP in the response ONLY in demo mode (no real SMTP)
        resp["demo_otp"] = otp
        resp["demo_note"] = "DEMO MODE: OTP returned in response (configure SMTP_HOST for real email)"

    return jsonify(resp), 200


@auth_bp.route("/verify-otp", methods=["POST"])
@jwt_required()
def verify_otp():
    """
    Validate the OTP submitted by the user.
    Returns 200 + a one-use token on success so the frontend can proceed with transfer.
    """
    import secrets
    username = get_jwt_identity()
    data     = request.get_json(silent=True) or {}
    entered  = (data.get("otp") or "").strip()

    if not entered or len(entered) != 6 or not entered.isdigit():
        return jsonify({"msg": "Enter the 6-digit verification code."}), 400

    record = _otp_store.get(username)
    if not record:
        return jsonify({"msg": "No OTP found. Please request a new code."}), 400

    record["attempts"] += 1

    if time.time() > record["expires_at"]:
        del _otp_store[username]
        return jsonify({"msg": "Verification code expired. Request a new one."}), 400

    if record["attempts"] > OTP_MAX_ATTEMPTS:
        del _otp_store[username]
        return jsonify({"msg": "Too many incorrect attempts. Request a new code."}), 429

    if entered != record["otp"]:
        remaining = OTP_MAX_ATTEMPTS - record["attempts"]
        return jsonify({"msg": f"Incorrect code. {remaining} attempt(s) remaining."}), 400

    # Success - consume OTP and issue a short-lived verification token
    del _otp_store[username]
    verify_token = secrets.token_urlsafe(32)
    # Store token so /transfer can validate it (expires in 10 min)
    _otp_store[f"vtok:{username}"] = {
        "token":      verify_token,
        "expires_at": time.time() + 600,
    }
    log.info("OTP verified for user %s", username)
    return jsonify({"verified": True, "verify_token": verify_token}), 200


def consume_verify_token(username: str, token: str) -> bool:
    """Called by payments.py to validate the OTP verify token before executing transfer."""
    record = _otp_store.get(f"vtok:{username}")
    if not record:
        return False
    if time.time() > record["expires_at"]:
        del _otp_store[f"vtok:{username}"]
        return False
    if record["token"] != token:
        return False
    del _otp_store[f"vtok:{username}"]
    return True
