# SafePay - Fraud-Protected Payments

> A production-deployed fintech simulator with JWT authentication, wallet payments, behavioural fraud detection, OTP verification, and transaction analytics.

<p align="center">
  <a href="https://safepay-lime.vercel.app">
    <strong>Live Demo</strong>
  </a>
  &nbsp;&nbsp;•&nbsp;&nbsp;
  <a href="https://safepay-hzxh.onrender.com/health">
    <strong>API Health</strong>
  </a>
</p>

---

## Overview

**SafePay** is a full-stack payment simulator designed around one core idea:

> **Every wallet transfer should be evaluated for fraud before it is completed.**

The application combines a Flask backend with a static JavaScript frontend. Before a transfer is approved, SafePay evaluates behavioural and transaction signals such as:

- New devices
- First-time recipients
- Unusual transaction amounts
- Rapid transaction activity
- Failed transaction attempts
- Late-night activity
- Unfamiliar locations
- Dormant accounts
- Multi-recipient activity
- Escalating transaction amounts

These signals are combined into a **risk score from 0–100**, which determines whether the transaction is approved, flagged, sent through OTP verification, or blocked.

### Live Application

**[Launch SafePay →](https://safepay-lime.vercel.app)**

> Portfolio/demo application — not intended for real financial transactions.

---

## Key Features

### Authentication & Security
- Username/password authentication
- PBKDF2-SHA256 password hashing
- JWT-based authentication
- JWT expiration and protected API routes
- Request rate limiting
- Parameterized SQL queries
- Security headers
- CORS restricted to the deployed frontend

### Wallet & Payments
- Wallet balance management
- Peer-to-peer transfers
- Razorpay payment integration
- Demo payment mode when Razorpay credentials are not configured
- Backend-only handling of payment secrets

### Behavioural Fraud Detection

Every transfer passes through a rule-based fraud scoring engine before completion.

The engine evaluates signals including:

| Signal | Example |
|---|---|
| New device | Transfer from an unfamiliar device |
| New recipient | First transfer to a recipient |
| Amount anomaly | Transaction significantly outside normal behaviour |
| Large transaction | High-value transfer |
| Velocity | Multiple transactions within a short period |
| Rapid-fire activity | Transactions occurring seconds apart |
| Failed attempts | Recent failed transactions |
| Late-night activity | Unusual transaction hour |
| Location anomaly | Activity from an unfamiliar location |
| Dormant account | Sudden activity after inactivity |
| Recipient fan-out | Transfers to multiple recipients |
| Escalating amounts | Increasing transaction values |

### Risk Levels

```text
Risk Score
    │
    ├── 0–29    → LOW
    │             Approve
    │
    ├── 30–54   → MEDIUM
    │             Approve + flag
    │
    ├── 55–74   → HIGH
    │             OTP verification
    │
    └── 75–100  → HIGH
                  Block transaction
