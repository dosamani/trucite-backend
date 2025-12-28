import os
import json
import hashlib
import uuid
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from flask import Flask, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

# ---------------------------------------------------------
# DB Helpers
# ---------------------------------------------------------

def mask_db_url(db_url: str) -> str:
    if not db_url:
        return ""
    try:
        if "://" in db_url and "@" in db_url:
            proto, rest = db_url.split("://", 1)
            creds, host = rest.split("@", 1)
            user = creds.split(":", 1)[0]
            return f"{proto}://{user}:***@{host}"
    except Exception:
        pass
    return db_url

def ensure_sslmode_require(db_url: str) -> str:
    """
    Supabase Postgres requires SSL. If sslmode not present, force sslmode=require.
    Works whether or not db_url already has query params.
    """
    if not db_url:
        return db_url

    parsed = urlparse(db_url)
    q = parse_qs(parsed.query)

    # If sslmode already set, keep it. Otherwise require it.
    if "sslmode" not in q:
        q["sslmode"] = ["require"]

    new_query = urlencode(q, doseq=True)
    rebuilt = parsed._replace(query=new_query)
    return urlunparse(rebuilt)

def get_db_conn():
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")

    db_url_ssl = ensure_sslmode_require(db_url)
    # connect_timeout prevents long stalls on cold start
    return psycopg2.connect(db_url_ssl, connect_timeout=10)

# ---------------------------------------------------------
# Database Startup Test
# ---------------------------------------------------------

def db_startup_ping():
    db_url = os.getenv("DATABASE_URL", "").strip()
    print(f"[DB] DATABASE_URL detected: {mask_db_url(db_url)}")

    conn = None
    cur = None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("select now();")
        ts = cur.fetchone()[0]
        print(f"[DB] Startup ping OK. Server time: {ts}")
    except Exception as e:
        print(f"[DB] Startup ping FAILED: {repr(e)}")
    finally:
        try:
            if cur: cur.close()
        except Exception:
            pass
        try:
            if conn: conn.close()
        except Exception:
            pass

db_startup_ping()

# ---------------------------------------------------------
# Utility
# ---------------------------------------------------------

def normalize_claim(text):
    return " ".join((text or "").lower().strip().split())

def fingerprint_claim(text):
    return hashlib.sha256((text or "").encode()).hexdigest()

# ---------------------------------------------------------
# API Endpoint
# ---------------------------------------------------------

@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")

    normalized = normalize_claim(text)
    claim_fp = fingerprint_claim(normalized)
    event_id = str(uuid.uuid4())

    score = 54
    verdict = "Questionable / High Uncertainty"

    response = {
        "audit_fingerprint": {
            "engine_version": "TruCite Claim Engine v2.4 (MVP)",
            "hash": claim_fp,
            "timestamp_utc": datetime.utcnow().isoformat() + "Z"
        },
        "claims": [{
            "confidence_weight": 1,
            "id": "c1",
            "text": text,
            "type": "factual"
        }],
        "event_id": event_id,
        "score": score,
        "verdict": verdict
    }

    conn = None
    cur = None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute(
            """
            insert into public.trucite_events
            (event_id, claim_fingerprint, normalized_claim, score, engine_version)
            values (%s, %s, %s, %s, %s)
            """,
            (event_id, claim_fp, normalized, score, "v2.4")
        )
        conn.commit()
        print("[DB] Insert OK")
    except Exception as e:
        print(f"[DB] Insert failed: {repr(e)}")
    finally:
        try:
            if cur: cur.close()
        except Exception:
            pass
        try:
            if conn: conn.close()
        except Exception:
            pass

    return jsonify(response)

# ---------------------------------------------------------
# Run
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
