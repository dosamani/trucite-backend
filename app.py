from flask import Flask, request, jsonify, Response
from datetime import datetime, timezone
import hashlib
import re

app = Flask(__name__)

SERVICE_NAME = "TruCite Backend"
MODE = "mvp_v2_reference_and_drift_stub"


# -------------------------
# Helpers
# -------------------------
def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def extract_claims(text: str):
    """
    Lightweight claim extraction for MVP:
    - Split by sentence-ish boundaries
    - Create a simple claim list
    """
    if not text:
        return []
    chunks = re.split(r"[.\n;]+", text.strip())
    claims = []
    idx = 1
    for c in chunks:
        c = c.strip()
        if len(c) < 6:
            continue
        claims.append({
            "id": f"c{idx}",
            "type": "factual",
            "text": c,
            "confidence_weight": 1
        })
        idx += 1
    return claims[:6]


def heuristic_score(text: str):
    """
    MVP scoring heuristic:
    - Penalize absolute claims without evidence cues
    - Penalize "always/never/100%" and sensational terms
    - Reward hedging/verification language
    """
    t = (text or "").lower()

    penalties = 0
    rewards = 0

    strong_terms = ["always", "never", "guaranteed", "proven", "100%", "everyone", "no doubt"]
    sensational = ["shocking", "secret", "exposed", "miracle", "cure", "instantly", "conspiracy"]
    hedges = ["may", "might", "could", "appears", "suggests", "likely", "unclear", "needs verification", "estimate"]

    for w in strong_terms:
        if w in t:
            penalties += 12
    for w in sensational:
        if w in t:
            penalties += 10
    for w in hedges:
        if w in t:
            rewards += 6

    # If no URLs/citations cues, penalize slightly
    if "http://" not in t and "https://" not in t and "source" not in t and "citation" not in t:
        penalties += 10

    base = 60
    score = base + rewards - penalties
    score = max(0, min(100, score))

    if score >= 75:
        verdict = "Plausible / Needs Verification"
    elif score >= 50:
        verdict = "Unclear / Mixed Confidence"
    else:
        verdict = "Questionable / High Uncertainty"

    return score, verdict


def reference_stub_for_claims(claims):
    """
    Reference grounding stub:
    In MVP, we return a placeholder reference object per claim
    to establish the API contract for future grounding.
    """
    refs = []
    for c in claims:
        refs.append({
            "claim_id": c["id"],
            "reference_type": "stub",
            "source_name": "Reference grounding not enabled in MVP",
            "url": None,
            "match_score": 0.0,
            "notes": "Next step: connect to curated sources + retrieval + citation mapping."
        })
    return refs


def drift_stub(text):
    """
    Model drift / change detection stub:
    Returns stable identifiers so later we can compare versions/events.
    """
    return {
        "drift_enabled": False,
        "content_fingerprint": stable_hash(text or ""),
        "baseline_fingerprint": None,
        "drift_score": None,
        "notes": "Next step: persist fingerprints + compare over time per source/model."
    }


# -------------------------
# Routes
# -------------------------
@app.get("/")
def home():
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>TruCite Backend</title>
  <style>
    body {{ font-family: Arial, sans-serif; background:#0b0b0b; color:#f3f3f3; padding:24px; }}
    .card {{ max-width:820px; margin:0 auto; background:#121212; border:1px solid #2a2a2a; border-radius:16px; padding:20px; }}
    h1 {{ margin:0 0 8px; font-size:22px; }}
    .muted {{ color:#b9b9b9; font-size:14px; line-height:1.4; }}
    code {{ background:#1d1d1d; padding:2px 6px; border-radius:6px; }}
    a {{ color:#ffd54a; text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    .row {{ margin-top:14px; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>TruCite Backend is Live</h1>
    <div class="muted">
      Service: <code>{SERVICE_NAME}</code><br/>
      Mode: <code>{MODE}</code><br/>
      Time (UTC): <code>{utc_now_iso()}</code>
    </div>

    <div class="row muted">
      Health: <code>GET /health</code><br/>
      Verify: <code>POST /verify</code> with JSON <code>{{"text":"..."}}</code>
    </div>

    <div class="row muted">
      This endpoint is the API. Your public landing page should live on the frontend service (e.g., <code>trucite-demo</code>).
    </div>
  </div>
</body>
</html>"""
    return Response(html, mimetype="text/html")


@app.get("/health")
def health():
    return jsonify({
        "service": SERVICE_NAME,
        "status": "ok",
        "mode": MODE,
        "routes": ["/", "/health", "/verify"],
        "time_utc": utc_now_iso()
    })


@app.post("/verify")
def verify():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "") or ""

    claims = extract_claims(text)
    score, verdict = heuristic_score(text)

    refs = reference_stub_for_claims(claims)
    drift = drift_stub(text)

    out = {
        "score": score,
        "verdict": verdict,
        "explanation": "MVP mode: heuristic scoring + claim extraction + reference-grounding (stub) + drift tracking (stub).",
        "claims": claims,
        "references": refs,
        "drift": drift,
        "meta": {
            "mode": MODE,
            "time_utc": utc_now_iso()
        }
    }
    return jsonify(out)
