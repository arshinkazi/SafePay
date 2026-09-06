"""
fraud_engine.py — Rule-Based Fraud Detection Engine (v3)
=========================================================
The ML model (LightGBM / IEEE-CIS features) requires C1-V339 Vesta features
that are not available at transaction time from our web frontend, so it always
scores near-zero and is excluded.  All scoring is done by a calibrated rule
engine whose combined score maps cleanly onto LOW / MEDIUM / HIGH / BLOCK.

Score ladder (additive, capped at 100):
  0–29   → LOW     → allow
  30–54  → MEDIUM  → warn, allow with confirmation
  55–74  → HIGH    → block until email OTP verified
  75+    → BLOCK   → hard reject, no bypass
"""

import os
import logging
import datetime
import math
from typing import NamedTuple

log = logging.getLogger("safepay.fraud")

# The ML model (LightGBM / IEEE-CIS features) requires Vesta features that are
# not available at transaction time from our web frontend (see module docstring),
# so it is intentionally excluded and scoring is rule-based only. This flag is
# surfaced via /health and /fraud-stats so the frontend can display ML status.
ML_AVAILABLE = False

# ── Thresholds ─────────────────────────────────────────────────────────────────
BLOCK_THRESHOLD  = 75
HIGH_THRESHOLD   = 55
MEDIUM_THRESHOLD = 30

# ── Individual rule weights ────────────────────────────────────────────────────
# Carefully chosen so realistic combinations reach the right tier:
#   Clean first txn from known device, normal amount, daytime  → ~12 (LOW)
#   New device + first recipient + round amount                → ~38 (MEDIUM)
#   Above + large amount                                       → ~55 (HIGH → OTP)
#   High velocity + rapid fire + large amount + new device     → ~82 (BLOCK)

W_NEW_DEVICE          = 15   # fingerprint not seen before for this user
W_FIRST_TIME_RECIP    = 14   # never sent to this recipient
W_ROUND_AMOUNT        = 10   # round multiples of 1 000 ≥ ₹5 000 (structuring)
W_UNUSUAL_AMOUNT      = 20   # > 4× 30-day average
W_LARGE_AMOUNT_50K    = 14   # ₹50 000 – ₹79 999
W_LARGE_AMOUNT_80K    = 22   # ₹80 000+
W_HIGH_VELOCITY       = 24   # ≥ 4 txns in last 60 min
W_RAPID_FIRE          = 22   # ≥ 2 txns in last 5 min
W_FAILED_RECENT       = 16   # ≥ 2 blocked/failed in last 24 h
W_LATE_NIGHT          = 14   # 23:00–04:59 UTC
W_UNUSUAL_LOCATION    = 32   # > 50 km from any known location
W_DORMANT_ACCOUNT     = 18   # no successful txn in ≥ 21 days
W_MULTI_RECIPIENT     = 12   # ≥ 3 distinct recipients today
W_ESCALATING_AMOUNTS  = 16   # last 3 txns strictly increasing, top > 3× bottom

LOCATION_TRUST_KM = 50


class FraudResult(NamedTuple):
    risk_score:       float
    ml_score:         float   # always 0.0 — kept for API compat
    rule_score:       float
    risk_level:       str
    risk_flags:       list
    should_block:     bool
    require_otp:      bool
    unusual_location: bool


# ── Helpers ────────────────────────────────────────────────────────────────────
def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _check_location(payload: dict, username: str, db):
    lat = payload.get("lat")
    lon = payload.get("lon")
    if lat is None or lon is None:
        return 0.0, [], False
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return 0.0, [], False

    known = db.execute(
        "SELECT lat, lon, visit_count FROM known_locations WHERE username=?",
        (username,)
    ).fetchall()

    if not known:
        db.execute(
            "INSERT INTO known_locations (username, lat, lon, visit_count) VALUES (?,?,?,1)",
            (username, lat, lon)
        )
        return 0.0, [], False

    min_dist = min(_haversine_km(lat, lon, r["lat"], r["lon"]) for r in known)

    if min_dist <= LOCATION_TRUST_KM:
        closest = min(known, key=lambda r: _haversine_km(lat, lon, r["lat"], r["lon"]))
        db.execute(
            """UPDATE known_locations
               SET last_seen=datetime('now'), visit_count=visit_count+1
               WHERE username=? AND lat=? AND lon=?""",
            (username, closest["lat"], closest["lon"])
        )
        return 0.0, [], False

    db.execute(
        "INSERT INTO known_locations (username, lat, lon, visit_count) VALUES (?,?,?,1)",
        (username, lat, lon)
    )
    return (
        float(W_UNUSUAL_LOCATION),
        [f"Unusual location — transaction initiated {min_dist:.0f} km from your usual area"],
        True,
    )


# ── Main rule engine ───────────────────────────────────────────────────────────
def _rule_score(payload: dict, username: str, db):
    score  = 0.0
    flags  = []
    now    = datetime.datetime.utcnow()
    amount = float(payload.get("TransactionAmt") or payload.get("amount") or 0)
    recip  = (payload.get("recipient") or "").strip().lower()

    from utils.security import make_device_hash
    device_hash = make_device_hash(payload)

    # ── 1. New / unrecognised device ──────────────────────────────────────────
    known_dev = db.execute(
        "SELECT id FROM known_devices WHERE username=? AND device_hash=?",
        (username, device_hash)
    ).fetchone()
    if not known_dev:
        score += W_NEW_DEVICE
        flags.append("Unrecognised device or browser detected for this account")

    # ── 2. First-time recipient ───────────────────────────────────────────────
    if recip:
        prev = db.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE username=? AND LOWER(recipient)=? AND status='success'",
            (username, recip)
        ).fetchone()[0]
        if prev == 0:
            score += W_FIRST_TIME_RECIP
            flags.append(f"First-ever transfer to '{recip}' — new recipient")

    # ── 3. Round-amount structuring ───────────────────────────────────────────
    if amount >= 5_000 and int(amount) % 1_000 == 0:
        score += W_ROUND_AMOUNT
        flags.append(f"Round amount ₹{amount:,.0f} — common in structured fraud")

    # ── 4. Amount vs 30-day personal average ──────────────────────────────────
    t30 = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    avg_row = db.execute(
        "SELECT AVG(amount) FROM transactions "
        "WHERE username=? AND status='success' AND created_at>=?",
        (username, t30)
    ).fetchone()
    avg = float(avg_row[0] or 0)
    if avg > 0 and amount > avg * 4:
        score += W_UNUSUAL_AMOUNT
        flags.append(
            f"Amount ₹{amount:,.0f} is {amount/avg:.1f}× your 30-day average "
            f"(₹{avg:,.0f})"
        )

    # ── 5. Absolute large-amount tiers ────────────────────────────────────────
    if amount >= 80_000:
        score += W_LARGE_AMOUNT_80K
        flags.append(f"Very high-value transfer: ₹{amount:,.0f} — mandatory review")
    elif amount >= 50_000:
        score += W_LARGE_AMOUNT_50K
        flags.append(f"High-value transfer: ₹{amount:,.0f} exceeds ₹50,000 threshold")

    # ── 6. High velocity (last 60 min) ────────────────────────────────────────
    t1h = (now - datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    n_1h = db.execute(
        "SELECT COUNT(*) FROM transactions "
        "WHERE username=? AND created_at>=? AND status!='blocked'",
        (username, t1h)
    ).fetchone()[0]
    if n_1h >= 4:
        score += W_HIGH_VELOCITY
        flags.append(f"High velocity — {n_1h} transactions in the last hour")

    # ── 7. Rapid-fire (last 5 min) ────────────────────────────────────────────
    t5m = (now - datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    n_5m = db.execute(
        "SELECT COUNT(*) FROM transactions WHERE username=? AND created_at>=?",
        (username, t5m)
    ).fetchone()[0]
    if n_5m >= 2:
        score += W_RAPID_FIRE
        flags.append(f"Rapid-fire pattern — {n_5m} transactions in the last 5 minutes")

    # ── 8. Recent failures / blocks ───────────────────────────────────────────
    t24h = (now - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    n_fail = db.execute(
        "SELECT COUNT(*) FROM transactions "
        "WHERE username=? AND status IN ('failed','blocked') AND created_at>=?",
        (username, t24h)
    ).fetchone()[0]
    if n_fail >= 2:
        score += W_FAILED_RECENT
        flags.append(f"{n_fail} failed or blocked transactions in the last 24 hours")

    # ── 9. Late-night / odd-hours ─────────────────────────────────────────────
    h = now.hour
    if h >= 23 or h < 5:
        score += W_LATE_NIGHT
        flags.append(f"Initiated at {now.strftime('%H:%M')} UTC — outside normal banking hours")

    # ── 10. Dormant account sudden activity ───────────────────────────────────
    last_ok = db.execute(
        "SELECT created_at FROM transactions "
        "WHERE username=? AND status='success' ORDER BY created_at DESC LIMIT 1",
        (username,)
    ).fetchone()
    if last_ok:
        try:
            last_dt = datetime.datetime.strptime(last_ok[0], "%Y-%m-%d %H:%M:%S")
            idle_days = (now - last_dt).days
            if idle_days >= 21:
                score += W_DORMANT_ACCOUNT
                flags.append(
                    f"Account inactive for {idle_days} days — sudden high-value activity"
                )
        except Exception:
            pass

    # ── 11. Multiple distinct recipients today ────────────────────────────────
    t_today = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    n_recip = db.execute(
        "SELECT COUNT(DISTINCT LOWER(recipient)) FROM transactions "
        "WHERE username=? AND created_at>=? AND status!='blocked' AND recipient!='WALLET_CREDIT'",
        (username, t_today)
    ).fetchone()[0]
    if n_recip >= 3:
        score += W_MULTI_RECIPIENT
        flags.append(f"Transferring to {n_recip} different recipients today — unusual fan-out")

    # ── 12. Escalating amounts (last 3 successful) ────────────────────────────
    recent = db.execute(
        "SELECT amount FROM transactions "
        "WHERE username=? AND status='success' ORDER BY created_at DESC LIMIT 3",
        (username,)
    ).fetchall()
    if len(recent) == 3:
        a1, a2, a3 = float(recent[0][0]), float(recent[1][0]), float(recent[2][0])
        if a3 > 0 and a2 > a3 and a1 > a2 and a1 > a3 * 3:
            score += W_ESCALATING_AMOUNTS
            flags.append(
                "Rapidly escalating transfer amounts — possible limit-probing behaviour"
            )

    # ── 13. Location ──────────────────────────────────────────────────────────
    loc_score, loc_flags, unusual_loc = _check_location(payload, username, db)
    score  += loc_score
    flags  += loc_flags

    return min(round(score, 2), 100.0), flags, unusual_loc


# ── Public entry point ─────────────────────────────────────────────────────────
def score_transaction(payload: dict, username: str, db) -> FraudResult:
    rule_score, flags, unusual_loc = _rule_score(payload, username, db)

    # Baseline: even a totally clean transaction should show a small score
    combined = max(rule_score, 5.0)

    should_block = combined >= BLOCK_THRESHOLD
    require_otp  = not should_block and combined >= HIGH_THRESHOLD

    if combined >= HIGH_THRESHOLD:
        level = "HIGH"
        if should_block:
            flags.insert(0, f"Risk score {combined:.0f}/100 — automatically blocked (threshold: {BLOCK_THRESHOLD})")
    elif combined >= MEDIUM_THRESHOLD:
        level = "MEDIUM"
    else:
        level = "LOW"

    log.info(
        "FraudScore[%s] rule=%.1f combined=%.1f level=%s block=%s otp=%s",
        username, rule_score, combined, level, should_block, require_otp,
    )

    return FraudResult(
        risk_score       = combined,
        ml_score         = 0.0,
        rule_score       = rule_score,
        risk_level       = level,
        risk_flags       = flags,
        should_block     = should_block,
        require_otp      = require_otp,
        unusual_location = unusual_loc,
    )
