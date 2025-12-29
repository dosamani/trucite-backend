import os
import uuid
import hashlib
from datetime import datetime, timezone

import psycopg2
from flask import Flask, request, jsonify, send_from_directory

# Flask default: serves ./static at /static/*
app = Flask(__name__, static_folder="static", static_url_path="/static")

ENGINE_VERSION = "TruCite Claim Engine v2.5 (MVP)"


# ---------------------------
# Helpers
# ---------------------------
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
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(db_url)


def ensure_table():
    """
    Creates trucite_events table if it doesn't exist.
    Safe to call repeatedly.
    """
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            create table if not exists public.trucite_events (
              id uuid primary key,
              event_id text,
              claim_text text,
              claim_hash text,
              score int,
              verdict text,
              engine_version text,
              created_at timestamptz default now()
            );
            """
        )
        conn.commit()
        cur.close()
        conn.close()
        print("[DB] ensure_table OK")
    except Exception as e:
        print("[DB] ensure_table FAILED:", str(e))


ensure_table()


# ---------------------------
# Routes
# ---------------------------
@app.get("/")
def landing():
    # Serve the landing page from ./static/index.html
    return send_from_directory(app.static_folder, "index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "ts": utc_now_iso()}), 200


@app.post("/verify")
def verify():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Missing 'text'"}), 400

    normalized = normalize_text(text)
    claim_hash = sha256_hex(normalized)
    event_id = payload.get("event_id") or str(uuid.uuid4())

    # MVP scoring placeholder (keep your current logic if you have better one)
    score = 54
    verdict = "Questionable / High Uncertainty"

    response = {
        "audit_fingerprint": {
            "engine_version": ENGINE_VERSION,
            "hash": claim_hash,
            "timestamp_utc": utc_now_iso(),
        },
        "claims": [
            {
                "confidence_weight": 1,
                "id": "c1",
                "text": text,
                "type": "factual",
            }
        ],
        "event_id": event_id,
        "score": score,
        "verdict": verdict,
    }

    # Log to DB (Supabase)
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            insert into public.trucite_events
            (id, event_id, claim_text, claim_hash, score, verdict, engine_version)
            values (%s, %s, %s, %s, %s, %s, %s);
            """,
            (str(uuid.uuid4()), event_id, text, claim_hash, score, verdict, ENGINE_VERSION),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        # Do not fail verify if logging fails
        print("[DB] insert FAILED:", str(e))

    return jsonify(response), 200


# Explicitly reject GET to /verify with a clear JSON (prevents confusing HTML 405 pages)
@app.get("/verify")
def verify_get():
    return jsonify({"error": "Use POST /verify"}), 405
