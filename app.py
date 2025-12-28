import os
import hashlib
import uuid
from datetime import datetime, timezone

from flask import Flask, request, jsonify
import psycopg2

app = Flask(__name__)

ENGINE_VERSION = "TruCite Claim Engine v2.5 (MVP)"


# ----------------------------
# Helpers
# ----------------------------

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
        raise RuntimeError("DATABASE_URL is not set in environment variables.")
    # NOTE: psycopg2 can accept postgresql:// URLs directly.
    return psycopg2.connect(db_url)

def ensure_table():
    """
    Creates the table if it doesn't exist.
    This runs on startup and is safe to call multiple times.
    """
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            create table if not exists public.trucite_events (
                id bigserial primary key,
                event_id text not null,
                claim_fingerprint text not null,
                normalized_claim text not null,
                score numeric not null,
                verdict text not null,
                engine_version text not null,
                created_at timestamptz not null default now()
            );
        """)
        cur.execute("create index if not exists idx_trucite_fp on public.trucite_events (claim_fingerprint);")
        conn.commit()
        cur.close()
        conn.close()
        print("[DB] ensure_table OK")
    except Exception as e:
        print(f"[DB] ensure_table FAILED: {repr(e)}")


def fetch_history_stats(claim_fp: str):
    """
    Returns:
      prior_runs: how many previous records exist for this fingerprint
      baseline_avg: avg(score) over all prior runs
      baseline_std: stddev_pop(score) over all prior runs (0 if only one)
    """
    conn = get_conn()
    cur = conn.cursor()

    # prior runs count
    cur.execute("""
        select count(*)
        from public.trucite_events
        where claim_fingerprint = %s;
    """, (claim_fp,))
    prior_runs = int(cur.fetchone()[0])

    # avg/stddev over prior runs
    cur.execute("""
        select
          avg(score)::float as avg_score,
          coalesce(stddev_pop(score)::float, 0.0) as std_score
        from public.trucite_events
        where claim_fingerprint = %s;
    """, (claim_fp,))
    row = cur.fetchone()
    baseline_avg = row[0]
    baseline_std = row[1] if row[1] is not None else 0.0

    cur.close()
    conn.close()

    return prior_runs, baseline_avg, baseline_std


def insert_event(event_id, claim_fp, normalized_claim, score, verdict):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        insert into public.trucite_events
          (event_id, claim_fingerprint, normalized_claim, score, verdict, engine_version)
        values
          (%s, %s, %s, %s, %s, %s);
    """, (event_id, claim_fp, normalized_claim, score, verdict, ENGINE_VERSION))
    conn.commit()
    cur.close()
    conn.close()


def drift_level_from_delta(delta: float, std: float, prior_runs: int):
    """
    Simple MVP drift classifier.
    """
    if prior_runs <= 1:
        return "None (no history)"

    # If std is near-zero, drift is purely delta-based
    if std is None or std < 0.0001:
        if abs(delta) >= 10:
            return "High"
        if abs(delta) >= 5:
            return "Moderate"
        return "Minimal"

    # Otherwise use delta vs std heuristic
    z = abs(delta) / std
    if z >= 2.0:
        return "High"
    if z >= 1.0:
        return "Moderate"
    return "Minimal"


# ----------------------------
# Startup
# ----------------------------
ensure_table()


# ----------------------------
# Routes
# ----------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "engine": ENGINE_VERSION})


@app.route("/verify", methods=["POST"])
def verify():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()

    if not text:
        return jsonify({
            "error": "Missing required field: text",
            "example": {"text": "Moon is 1km from earth and made of candy"}
        }), 400

    normalized = normalize_text(text)
    claim_fp = sha256_hex(normalized)
    event_id = str(uuid.uuid4())

    # MVP scoring (placeholder logic)
    score = 54
    verdict = "Questionable / High Uncertainty"

    # Read history BEFORE inserting this event
    try:
        prior_runs, baseline_avg, baseline_std = fetch_history_stats(claim_fp)
    except Exception as e:
        print(f"[DB] fetch_history_stats failed: {repr(e)}")
        prior_runs, baseline_avg, baseline_std = 0, None, None

    # Insert this event
    try:
        insert_event(event_id, claim_fp, normalized, score, verdict)
    except Exception as e:
        print(f"[DB] insert_event failed: {repr(e)}")

    # Compute drift deltas (vs baseline avg)
    drift_delta = None
    drift_level = "None (no history)"
    note = "No prior audit history for this claim yet."

    if baseline_avg is not None:
        drift_delta = float(score) - float(baseline_avg)
        drift_level = drift_level_from_delta(drift_delta, baseline_std or 0.0, prior_runs + 1)
        note = "Baseline computed from prior runs of the same normalized claim text."

    response = {
        "audit_fingerprint": {
            "engine_version": ENGINE_VERSION,
            "hash": claim_fp,
            "timestamp_utc": utc_now_iso()
        },
        "claims": [{
            "confidence_weight": 1,
            "id": "c1",
            "text": text,
            "type": "factual"
        }],
        "content_fingerprint": {
            "normalized_claim": normalized,
            "claim_fingerprint": claim_fp
        },
        "event_id": event_id,

        # New: drift output
        "prior_runs": prior_runs + 1,  # include this run
        "drift_summary": {
            "prior_runs": prior_runs,
            "baseline_score_avg": baseline_avg,
            "baseline_score_std": baseline_std,
            "drift_delta": drift_delta,
            "drift_level": drift_level,
            "note": note
        },

        "score": score,
        "verdict": verdict
    }

    return jsonify(response)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
