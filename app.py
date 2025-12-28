import os
import hashlib
import uuid
from datetime import datetime

from flask import Flask, request, jsonify
import psycopg2

app = Flask(__name__)

# ---------------------------------------------------------
# DB helpers
# ---------------------------------------------------------

def get_db_url():
    """
    Render/Supabase DSN is expected in DATABASE_URL.
    Example:
    postgresql://user:password@host:5432/postgres
    """
    return os.getenv("DATABASE_URL", "").strip()

def mask_db_url(db_url: str) -> str:
    """Mask password in logs."""
    try:
        if "://" in db_url and "@" in db_url:
            proto, rest = db_url.split("://", 1)
            creds, host = rest.split("@", 1)
            user = creds.split(":", 1)[0]
            return f"{proto}://{user}:***@{host}"
    except Exception:
        pass
    return db_url

def db_startup_ping():
    db_url = get_db_url()
    if not db_url:
        print("[DB] DATABASE_URL is missing or empty.")
        return

    try:
        print(f"[DB] DATABASE_URL detected: {mask_db_url(db_url)}")
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

def normalize_claim(text: str) -> str:
    return " ".join((text or "").lower().strip().split())

def fingerprint_claim(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

# ---------------------------------------------------------
# API
# ---------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "ts_utc": datetime.utcnow().isoformat() + "Z"})

@app.route("/verify", methods=["POST"])
def verify():
    # ---- Parse request
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")

    normalized = normalize_claim(text)
    claim_fp = fingerprint_claim(normalized)
    event_id = str(uuid.uuid4())

    # ---- MVP scoring stub
    score = 54
    verdict = "Questionable / High Uncertainty"

    prior_runs = 0

    # ---- DB work (count prior runs + insert event)
    db_url = get_db_url()
    if db_url:
        try:
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()

            # Count how many times we've seen this exact fingerprint before
            cur.execute(
                "select count(*) from public.trucite_events where claim_fingerprint = %s",
                (claim_fp,)
            )
            prior_runs = int(cur.fetchone()[0])

            # Insert this event
            cur.execute("""
                insert into public.trucite_events
                (event_id, claim_fingerprint, normalized_claim, score, engine_version)
                values (%s, %s, %s, %s, %s)
            """, (event_id, claim_fp, normalized, score, "v2.4"))

            conn.commit()
            cur.close()
            conn.close()

        except Exception as e:
            print(f"[DB] Insert/count failed: {repr(e)}")
    else:
        print("[DB] DATABASE_URL missing; running without persistence.")

    # ---- Response
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
        "prior_runs": prior_runs,
        "score": score,
        "verdict": verdict
    }

    return jsonify(response)

# ---------------------------------------------------------
# Run
# ---------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
