import os
import re
import json
import uuid
import hashlib
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory
import psycopg2

app = Flask(__name__, static_folder="static", static_url_path="/static")

# ---------------------------------------------------------
# Config
# ---------------------------------------------------------

ENGINE_VERSION = "TruCite Claim Engine v2.5 (MVP)"

# Allowlist: keep this conservative for MVP.
# You can expand later (nih.gov, sec.gov, etc.).
TRUSTED_DOMAINS = {
    "cdc.gov",
    "www.cdc.gov",
    "nih.gov",
    "www.nih.gov",
    "who.int",
    "www.who.int",
    "fda.gov",
    "www.fda.gov",
}

WIKIPEDIA_BLOCKLIST = {
    "wikipedia.org",
    "www.wikipedia.org",
    "en.wikipedia.org",
}

URL_REGEX = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)


# ---------------------------------------------------------
# Static serving (landing page)
# ---------------------------------------------------------

@app.route("/", methods=["GET"])
def serve_index():
    # index.html MUST be inside ./static/
    return send_from_directory(app.static_folder, "index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------
# Utilities
# ---------------------------------------------------------

def utc_iso():
    return datetime.now(timezone.utc).isoformat()

def sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()

def short_event_id(sha: str) -> str:
    # Keeps your current style: short ID derived from hash
    return (sha or "")[:12]

def extract_claims(text: str):
    # MVP: one claim = full text (claim splitting is Step 2)
    cleaned = (text or "").strip()
    return [{"text": cleaned}]

def score_heuristic(text: str):
    """
    Keep this intentionally simple/stable for MVP.
    We are NOT changing scoring behavior here beyond minor determinism.
    """
    t = (text or "").strip().lower()

    if not t:
        return 54, "Unclear / needs verification"

    # Obvious absurdity cues (very lightweight)
    absurd_markers = ["made of candy", "1km from earth", "flat earth", "lizard people"]
    if any(m in t for m in absurd_markers):
        return 55, "Unclear / needs verification"

    # If user adds a “Source:” line, we don’t automatically trust it (handled via refs stub)
    return 72, "Plausible / Needs Verification"

def extract_references_allowlist(text: str):
    """
    Step 1: References stub.
    - If trusted URLs appear, include them.
    - If Wikipedia appears, explicitly note it is blocked.
    - Otherwise references=[] and note says allowlist mode.
    """
    refs = []
    urls = URL_REGEX.findall(text or "")

    wikipedia_found = False

    for u in urls:
        # Normalize: strip trailing punctuation
        url = u.rstrip(").,;!]")
        # crude domain extraction
        domain = url.split("//", 1)[-1].split("/", 1)[0].lower()

        if any(bad in domain for bad in WIKIPEDIA_BLOCKLIST):
            wikipedia_found = True
            continue

        # allow exact or suffix match (cdc.gov matches subdomains)
        if domain in TRUSTED_DOMAINS or any(domain.endswith("." + d) for d in TRUSTED_DOMAINS):
            refs.append({"domain": domain, "url": url})

    if wikipedia_found:
        note = "Reference grounding in allowlist mode: only trusted domains are permitted. Wikipedia blocked."
    else:
        note = "Reference grounding in allowlist mode: only trusted domains are permitted."

    return note, refs


def db_insert_event(event_id: str, sha: str, text: str, score: int, verdict: str):
    """
    Optional: only runs if DATABASE_URL exists.
    Does not break the app if DB is unavailable.
    """
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        return

    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        # Table expected:
        # public.trucite_events(event_id text, sha256 text, claim_text text, score int, verdict text, created_utc timestamptz)
        cur.execute("""
            insert into public.trucite_events
            (event_id, sha256, claim_text, score, verdict, created_utc)
            values (%s, %s, %s, %s, %s, now())
        """, (event_id, sha, text, score, verdict))

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] Insert failed: {repr(e)}")


# ---------------------------------------------------------
# API
# ---------------------------------------------------------

@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    sha = sha256_text(text)
    event_id = short_event_id(sha)

    score, verdict = score_heuristic(text)

    # Step 1: reference stub
    reference_note, references = extract_references_allowlist(text)

    response = {
        "audit_fingerprint": {
            "sha256": sha,
            "timestamp_utc": utc_iso(),
        },
        "claims": extract_claims(text),
        "event_id": event_id,
        "explanation": (
            "MVP heuristic score. This demo evaluates linguistic certainty/uncertainty cues and basic risk signals. "
            "Replace with evidence-backed verification in production."
        ),
        "reference_note": reference_note,
        "references": references,
        "score": score,
        "verdict": verdict,
    }

    # Optional persistence
    db_insert_event(event_id, sha, text, score, verdict)

    return jsonify(response), 200


# ---------------------------------------------------------
# Run
# ---------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
