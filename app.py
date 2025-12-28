import os
import hashlib
import uuid
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory
import psycopg2

# Serve files from ./static (index.html, script.js, style.css, logo.jpg)
app = Flask(__name__, static_folder="static", static_url_path="")

ENGINE_VERSION = "TruCite Claim Engine v2.5 (MVP)"


# -----------------------------
# Helpers
# -----------------------------
def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def normalize_text(text: str) -> str:
    return " ".join((text or "").lower().strip().split())

def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def get_db_url():
    return os.getenv("DATABASE_URL", "").strip()

def get_conn():
    db_url = get_db_url()
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg2.connect(db_url)


# -----------------------------
# Static routes
# -----------------------------
@app.get("/")
def home():
    # serve static/index.html
    return send_from_directory(app.static_folder, "index.html")

@app.get("/<path:filename>")
def static_files(filename):
    # serve anything else in /static (script.js, style.css, images)
    return send_from_directory(app.static_folder, filename)


# -----------------------------
# DB setup (safe)
# -----------------------------
def ensure_table():
    """
    Creates the table if it doesn't exist.
    Safe to run on every startup.
    """
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            create table if not exists public.trucite_events (
                id bigserial primary key,
                created_at timestamptz not null default now(),
                event_id uuid not null,
                claim_fingerprint text not null,
                normalized_claim text not null,
                score integer not null,
                engine_version text not null
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("[DB] ensure_table OK")
    except Exception as e:
        print(f"[DB] ensure_table FAILED: {repr(e)}")

ensure_table()


# -----------------------------
# API Endpoint
# -----------------------------
@app.post("/verify")
def verify():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    normalized = normalize_text(text)
    claim_fp = sha256_hex(normalized)
    event_id = str(uuid.uuid4())

    # Placeholder scoring (MVP)
    score = 54
    verdict = "Questionable / High Uncertainty"

    response = {
        "audit_fingerprint": {
            "engine_version": ENGINE_VERSION,
            "hash": claim_fp,
            "timestamp_utc": utc_now_iso(),
        },
        "claims": [{
            "confidence_weight": 1,
            "id": "c1",
            "text": text,
            "type": "factual",
        }],
        "event_id": event_id,
        "score": score,
        "verdict": verdict,
    }

    # Insert into DB (best-effort)
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            insert into public.trucite_events
            (event_id, claim_fingerprint, normalized_claim, score, engine_version)
            values (%s, %s, %s, %s, %s)
        """, (event_id, claim_fp, normalized, score, ENGINE_VERSION))
        conn.commit()
        cur.close()
        conn.close()
        print(f"[DB] Insert OK event_id={event_id}")
    except Exception as e:
        print(f"[DB] Insert FAILED: {repr(e)}")

    return jsonify(response)


# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
