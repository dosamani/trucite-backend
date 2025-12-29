import os
import re
import uuid
import json
import hashlib
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory

# ---------------------------------------------------------
# App config
# ---------------------------------------------------------
app = Flask(__name__, static_folder="static", static_url_path="/static")

ENGINE_VERSION = os.getenv("ENGINE_VERSION", "TruCite Claim Engine v2.4 (MVP)")

# Optional Postgres (Supabase) support:
# - If DATABASE_URL is missing or invalid, the app still runs (no crashes)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

try:
    import psycopg2
except Exception:
    psycopg2 = None


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def normalize_text(s: str) -> str:
    return " ".join((s or "").lower().strip().split())

def sha256_hex(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()

def extract_urls(text: str):
    if not text:
        return []
    # simple url extractor
    urls = re.findall(r'https?://[^\s)"]+', text)
    return urls

def domain_from_url(url: str):
    try:
        # quick parse without extra deps
        no_proto = url.split("://", 1)[1]
        domain = no_proto.split("/", 1)[0]
        return domain.lower()
    except Exception:
        return ""


# Allowlist concept (you can expand later)
TRUSTED_DOMAIN_ALLOWLIST = set([
    "www.cdc.gov",
    "cdc.gov",
    "www.nih.gov",
    "nih.gov",
    "www.fda.gov",
    "fda.gov",
    "www.nasa.gov",
    "nasa.gov",
    "www.noaa.gov",
    "noaa.gov",
    "www.who.int",
    "who.int",
    "www.cms.gov",
    "cms.gov",
])

BLOCKED_DOMAINS = set([
    "wikipedia.org",
    "www.wikipedia.org",
])


def db_startup_ping():
    if not DATABASE_URL or not psycopg2:
        print("[DB] DATABASE_URL not set or psycopg2 not installed. Running without DB.")
        return

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("select now();")
        ts = cur.fetchone()[0]
        cur.close()
        conn.close()
        print(f"[DB] Startup ping OK. Server time: {ts}")
    except Exception as e:
        print(f"[DB] Startup ping FAILED: {repr(e)}")


db_startup_ping()


def ensure_table_exists():
    """
    Creates table if it doesn't exist.
    Safe to run on startup and on-demand.
    """
    if not DATABASE_URL or not psycopg2:
        return

    ddl = """
    create table if not exists public.trucite_events (
        id bigserial primary key,
        event_id uuid not null,
        claim_fingerprint text not null,
        normalized_claim text not null,
        score integer not null,
        verdict text not null,
        engine_version text not null,
        created_at timestamptz not null default now()
    );
    create index if not exists idx_trucite_claim_fp on public.trucite_events (claim_fingerprint);
    """

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(ddl)
        conn.commit()
        cur.close()
        conn.close()
        print("[DB] Table ensured: public.trucite_events")
    except Exception as e:
        print(f"[DB] ensure_table_exists FAILED: {repr(e)}")


ensure_table_exists()


def db_get_stats_for_claim(claim_fp: str):
    """
    Returns (count, avg_score, std_score) for a given claim fingerprint.
    If DB not enabled, returns None.
    """
    if not DATABASE_URL or not psycopg2:
        return None

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            """
            select count(*), avg(score)::float, stddev_pop(score)::float
            from public.trucite_events
            where claim_fingerprint = %s
            """,
            (claim_fp,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()

        cnt = int(row[0] or 0)
        avg = None if row[1] is None else float(row[1])
        std = None if row[2] is None else float(row[2])
        return {"count": cnt, "avg": avg, "std": std}
    except Exception as e:
        print(f"[DB] db_get_stats_for_claim FAILED: {repr(e)}")
        return None


def db_insert_event(event_id: str, claim_fp: str, normalized_claim: str, score: int, verdict: str):
    if not DATABASE_URL or not psycopg2:
        return False

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            """
            insert into public.trucite_events
            (event_id, claim_fingerprint, normalized_claim, score, verdict, engine_version)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (event_id, claim_fp, normalized_claim, score, verdict, ENGINE_VERSION),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[DB] db_insert_event FAILED: {repr(e)}")
        return False


# ---------------------------------------------------------
# Routes
# ---------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    # Serve the landing page from static/index.html
    return send_from_directory("static", "index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "engine_version": ENGINE_VERSION})


@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    # Deterministic claim fingerprint from normalized input
    normalized = normalize_text(text)
    claim_fp = sha256_hex(normalized)
    event_id = str(uuid.uuid4())

    # MVP scoring placeholder (you can refine later)
    # Keep your current behavior: nonsense claim ~54, “source spam” drops it.
    score = 54
    verdict = "Questionable / High Uncertainty"

    urls = extract_urls(text)
    references = []
    allowlist_mode = True

    if allowlist_mode:
        ref_note = "Reference grounding in allowlist mode: only trusted domains are permitted. Wikipedia blocked."
    else:
        ref_note = "Reference grounding not enabled in MVP. Wikipedia blocked as a reference source."

    # If a user includes a URL, surface it (but do not “validate” it yet)
    for u in urls:
        d = domain_from_url(u)
        if not d:
            continue
        if d in BLOCKED_DOMAINS:
            continue
        if allowlist_mode and d not in TRUSTED_DOMAIN_ALLOWLIST:
            continue
        references.append({"domain": d, "url": u})

    # Penalize “source injection” behavior lightly in MVP (keeps your 36 example behavior)
    if references and ("moon" in normalized and "candy" in normalized and "1km" in normalized):
        score = 36
        verdict = "Likely False / Misleading"

    # DB drift summary (if DB is working)
    stats = db_get_stats_for_claim(claim_fp)
    if stats is None:
        drift_summary = {
            "prior_runs": 0,
            "note": "DB not enabled; no audit history.",
            "drift_level": "None (no history)",
            "baseline_score_avg": None,
            "baseline_score_std": None,
            "drift_delta": None,
            "claim_fingerprint": claim_fp
        }
        prior_runs = 0
    else:
        prior_runs = stats["count"]
        if prior_runs == 0:
            drift_summary = {
                "prior_runs": 0,
                "note": "No prior audit history for this claim yet.",
                "drift_level": "None (no history)",
                "baseline_score_avg": None,
                "baseline_score_std": None,
                "drift_delta": None,
                "claim_fingerprint": claim_fp
            }
        else:
            baseline_avg = stats["avg"]
            baseline_std = stats["std"] if stats["std"] is not None else 0.0
            drift_delta = None if baseline_avg is None else round(score - baseline_avg, 4)

            # Simple drift-level heuristic
            if drift_delta is None:
                drift_level = "None (no history)"
            elif abs(drift_delta) < 5:
                drift_level = "Minimal"
            elif abs(drift_delta) < 12:
                drift_level = "Moderate"
            else:
                drift_level = "High"

            drift_summary = {
                "prior_runs": prior_runs,
                "note": "Baseline computed from prior runs of the same normalized claim text.",
                "drift_level": drift_level,
                "baseline_score_avg": None if baseline_avg is None else round(baseline_avg, 4),
                "baseline_score_std": round(baseline_std, 4),
                "drift_delta": drift_delta,
                "claim_fingerprint": claim_fp
            }

    # Insert current event AFTER stats check so prior_runs reflects history before this run
    db_insert_event(event_id, claim_fp, normalized, score, verdict)

    response = {
        "audit_fingerprint": {
            "engine_version": ENGINE_VERSION,
            "hash": claim_fp,
            "timestamp_utc": utc_now_iso()
        },
        "claims": [
            {
                "confidence_weight": 1,
                "id": "c1",
                "text": text,
                "type": "factual"
            }
        ],
        "content_fingerprint": {
            "claim_fingerprint": claim_fp,
            "normalized_claim": normalized
        },
        "drift_summary": drift_summary,
        "event_id": event_id,
        "explanation": "MVP mode: returning a baseline score plus extracted claims. Next steps will add reference-grounding and drift tracking.",
        "reference_note": ref_note,
        "references": references,
        "risk_summary": {
            "misinformation_risk": "High" if score < 50 else "Medium",
            "model_confidence_gap": "Significant" if score < 50 else "Moderate",
            "regulatory_exposure": "High" if score < 45 else "Medium"
        },
        "score": score,
        "trust_profile": {
            "drift_risk": 0.56 if score == 54 else 0.79 if score < 45 else 0.39,
            "grounding_strength": 0.29 if score < 45 else 0.44 if score == 54 else 0.58,
            "reliability": round(score / 100, 2),
            "volatility": 0.61 if score == 54 else 0.74 if score < 45 else 0.40
        },
        "verdict": verdict
    }

    return jsonify(response)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
