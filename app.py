import os
import uuid
import hashlib
from datetime import datetime, timezone

from flask import send_from_directory

@app.get("/")
def home():
    return send_from_directory("static", "index.html")


# ============================================================
# App config
# ============================================================
app = Flask(__name__, static_folder="static", static_url_path="/static")

ENGINE_VERSION = os.getenv("ENGINE_VERSION", "TruCite Claim Engine v2.5 (MVP)")


# ============================================================
# Helpers
# ============================================================
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_text(text: str) -> str:
    return " ".join((text or "").lower().strip().split())


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_db_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def get_conn():
    db_url = get_db_url()
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    # psycopg2 supports postgres:// and postgresql:// URLs
    return psycopg2.connect(db_url)


def ensure_table():
    """
    Creates the trucite_events table if it doesn't exist.
    Safe to run multiple times.
    """
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            create table if not exists public.trucite_events (
                id uuid primary key,
                created_at timestamptz not null default now(),
                event_id uuid not null,
                engine_version text not null,
                claim_text text not null,
                claim_hash text not null,
                score int not null,
                verdict text not null,
                raw_payload jsonb not null
            );
            """
        )
        conn.commit()
        cur.close()
        conn.close()
        print("[DB] ensure_table OK")
    except Exception as e:
        # Don't crash boot if DB isn't ready; log it
        print(f"[DB] ensure_table ERROR: {e}")


def log_event_to_db(event_id: str, claim_text: str, score: int, verdict: str, raw_payload: dict):
    """
    Inserts an event row into Postgres (Supabase).
    """
    try:
        conn = get_conn()
        cur = conn.cursor()

        row_id = str(uuid.uuid4())
        claim_hash = sha256_hex(normalize_text(claim_text))

        cur.execute(
            """
            insert into public.trucite_events
                (id, event_id, engine_version, claim_text, claim_hash, score, verdict, raw_payload)
            values
                (%s, %s, %s, %s, %s, %s, %s, %s::jsonb);
            """,
            (row_id, event_id, ENGINE_VERSION, claim_text, claim_hash, int(score), verdict, jsonify_safe_json(raw_payload)),
        )

        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[DB] insert ERROR: {e}")
        return False


def jsonify_safe_json(payload: dict) -> str:
    """
    psycopg2 wants JSON as a string for %s::jsonb.
    Flask jsonify returns a Response; so we do a proper JSON string.
    """
    import json
    return json.dumps(payload, ensure_ascii=False)


def simple_score_and_verdict(claim_text: str) -> tuple[int, str]:
    """
    MVP placeholder scoring.
    Replace this with your real claim parsing + reference engine logic later.

    Current behavior:
    - If claim contains obvious absurd markers -> very low score
    - Else moderate uncertainty default
    """
    t = normalize_text(claim_text)

    absurd_markers = ["made of candy", "1km from earth", "flat earth", "moon is cheese", "reptilian"]
    if any(m in t for m in absurd_markers):
        return 12, "False / Very Low Confidence"

    # Default MVP behavior
    return 54, "Questionable / High Uncertainty"


# ============================================================
# Routes: Landing + Static
# ============================================================
@app.get("/")
def home():
    # Serve the landing page from static/index.html
    return send_from_directory(app.static_folder, "index.html")


@app.get("/static/<path:filename>")
def static_files(filename):
    # Explicit static route (helps prevent misconfig issues)
    return send_from_directory(app.static_folder, filename)


# ============================================================
# API routes
# ============================================================
@app.get("/health")
def health():
    return jsonify({"status": "ok", "engine_version": ENGINE_VERSION})


@app.post("/verify")
def verify():
    data = request.get_json(silent=True) or {}

    # Accept either {"text": "..."} or a richer payload with claims[0].text
    claim_text = (data.get("text") or "").strip()

    if not claim_text and isinstance(data.get("claims"), list) and len(data["claims"]) > 0:
        claim_text = (data["claims"][0].get("text") or "").strip()

    if not claim_text:
        return jsonify({"error": "Missing claim text. Send {\"text\":\"...\"} or {\"claims\":[{\"text\":\"...\"}]}"}), 400

    # Compute MVP score/verdict
    score, verdict = simple_score_and_verdict(claim_text)

    event_id = str(uuid.uuid4())
    fingerprint_hash = sha256_hex(normalize_text(claim_text))

    response_payload = {
        "event_id": event_id,
        "score": score,
        "verdict": verdict,
        "audit_fingerprint": {
            "engine_version": ENGINE_VERSION,
            "hash": fingerprint_hash,
            "timestamp_utc": utc_now_iso(),
        },
        "claims": [
            {
                "id": "c1",
                "type": "factual",
                "confidence_weight": 1,
                "text": claim_text,
            }
        ],
    }

    # Log to DB if configured
    if get_db_url():
        inserted = log_event_to_db(event_id, claim_text, score, verdict, response_payload)
        response_payload["db_logged"] = bool(inserted)
    else:
        response_payload["db_logged"] = False
        response_payload["db_note"] = "DATABASE_URL not set; skipping insert"

    return jsonify(response_payload), 200


# ============================================================
# Boot
# ============================================================
ensure_table()

if __name__ == "__main__":
    # Local dev only; Render uses gunicorn
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
