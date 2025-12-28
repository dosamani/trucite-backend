import os
import json
import hashlib
import uuid
from datetime import datetime

from flask import Flask, request, jsonify
import psycopg2

app = Flask(__name__)

# ---------------------------------------------------------
# Database Startup Test
# ---------------------------------------------------------

def db_startup_ping():
    db_url = os.getenv("DATABASE_URL", "")

    try:
        masked = db_url
        if "://" in db_url and "@" in db_url:
            proto, rest = db_url.split("://", 1)
            creds, host = rest.split("@", 1)
            user = creds.split(":", 1)[0]
            masked = f"{proto}://{user}:***@{host}"

        print(f"[DB] DATABASE_URL detected: {masked}")

        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("select now();")
        ts = cur.fetchone()[0]
        cur.close()
        conn.close()

        print(f"[DB] Startup ping OK. Server time: {ts}")

    except Exception as e:
        print(f"[DB] Startup ping FAILED: {repr(e)}")

db_startup_ping()

# ---------------------------------------------------------
# Utility
# ---------------------------------------------------------

def normalize_claim(text):
    return " ".join(text.lower().strip().split())

def fingerprint_claim(text):
    return hashlib.sha256(text.encode()).hexdigest()

# ---------------------------------------------------------
# API Endpoint
# ---------------------------------------------------------

@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json()
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

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("""
            insert into public.trucite_events
            (event_id, claim_fingerprint, normalized_claim, score, engine_version)
            values (%s, %s, %s, %s, %s)
        """, (event_id, claim_fp, normalized, score, "v2.4"))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] Insert failed: {repr(e)}")

    return jsonify(response)

# ---------------------------------------------------------
# Run
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
