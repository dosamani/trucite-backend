import os
import time
import hashlib
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Optional local modules (safe fallbacks if not present)
try:
    from claim_parser import extract_claims
except Exception:
    extract_claims = None

try:
    from reference_engine import score_claim_text
except Exception:
    score_claim_text = None


app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)


# -------------------------
# Static landing page
# -------------------------
@app.get("/")
def landing():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


# -------------------------
# Health check
# -------------------------
@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "trucite-backend", "ts": int(time.time())})


# -------------------------
# Verify endpoint
# -------------------------
@app.route("/verify", methods=["POST", "OPTIONS"])
def verify():
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    evidence = (payload.get("evidence") or "").strip()
    policy_mode = (payload.get("policy_mode") or "enterprise").strip()

    if not text:
        return jsonify({"error": "Missing 'text' in request body"}), 400

    # Fingerprint / Event ID
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    event_id = sha[:12]
    ts = datetime.now(timezone.utc).isoformat()

    # Claims extraction
    claims = []
    if extract_claims:
        try:
            extracted = extract_claims(text)
            if isinstance(extracted, list):
                for c in extracted:
                    if isinstance(c, dict) and "text" in c:
                        claims.append({"text": str(c["text"])})
                    elif isinstance(c, str):
                        claims.append({"text": c})
            elif isinstance(extracted, str):
                claims = [{"text": extracted}]
        except Exception:
            claims = [{"text": text}]
    else:
        claims = [{"text": text}]

    # References (normalize evidence into a structured list)
    references = normalize_references(evidence)

    # Scoring
    if score_claim_text:
        try:
            score, verdict, explanation, signals = score_claim_text(text)  # allow extended return
            if not isinstance(signals, dict):
                signals = {}
        except Exception:
            score, verdict, explanation, signals = heuristic_score(text, references)
    else:
        score, verdict, explanation, signals = heuristic_score(text, references)

    # Decision Gate
    if score >= 75:
        action = "ALLOW"
        reason = "High confidence per current MVP scoring."
    elif score >= 55:
        action = "REVIEW"
        reason = "Medium confidence. Human verification recommended."
    else:
        action = "BLOCK"
        reason = "Low confidence. Do not use without verification."

    resp = {
        "verdict": verdict,
        "score": int(score),
        "decision": {"action": action, "reason": reason},
        "event_id": event_id,
        "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},
        "claims": claims,
        "explanation": explanation,
        "references": references,     # always present for frontend “details” panel
        "signals": signals,           # extra debug/telemetry (won’t break UI)
        "policy_mode": policy_mode
    }

    return jsonify(resp), 200


# -------------------------
# Reference normalization
# -------------------------
def normalize_references(evidence_text: str):
    """
    Accepts evidence pasted by the user (URLs / DOI / PMID).
    Returns a list of {"type": "...", "value": "..."}.
    """
    ev = (evidence_text or "").strip()
    if not ev:
        return []

    refs = []
    parts = [p.strip() for p in ev.replace("\n", " ").split() if p.strip()]

    for p in parts:
        v = p.strip().strip(",;")
        low = v.lower()

        if low.startswith("http://") or low.startswith("https://"):
            refs.append({"type": "url", "value": v})
            continue

        # DOI patterns (simple)
        if low.startswith("doi:"):
            refs.append({"type": "doi", "value": v[4:].strip()})
            continue
        if "10." in low and "/" in low and len(low) >= 8:
            # Avoid catching random "10.x" numbers by requiring a slash
            refs.append({"type": "doi", "value": v})
            continue

        # PMID patterns
        if low.startswith("pmid:"):
            refs.append({"type": "pmid", "value": v[5:].strip()})
            continue
        if v.isdigit() and len(v) in (7, 8):
            # Many PMIDs are 7–8 digits; not perfect but useful for MVP
            refs.append({"type": "pmid", "value": v})
            continue

        # fallback blob
        refs.append({"type": "text", "value": v})

    # de-dupe while preserving order
    seen = set()
    uniq = []
    for r in refs:
        key = (r.get("type", ""), r.get("value", ""))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


# -------------------------
# MVP heuristic scoring + semantic sanity
# -------------------------
def heuristic_score(text: str, references=None):
    """
    MVP heuristic scoring (0-100).
    Adds a lightweight "semantic plausibility" signal to flag obviously nonsensical claims
    without calling external systems.

    Returns: (score, verdict, explanation, signals)
    """
    references = references or []
    t = (text or "").strip()
    tl = t.lower()

    risky_words = ["always", "never", "guaranteed", "cure", "cures", "100%", "proof", "definitely"]
    hedges = ["may", "might", "could", "likely", "possibly", "suggests", "uncertain"]
    has_digit = any(ch.isdigit() for ch in t)
    has_refs = len(references) > 0

    score = 55
    risk_flags = []

    # Linguistic risk cues
    if any(w in tl for w in risky_words):
        score -= 15
        risk_flags.append("high_certainty_language")

    if any(w in tl for w in hedges):
        score += 10
        risk_flags.append("hedging_language")

    # Very long text tends to be lower confidence for MVP
    if len(t) > 800:
        score -= 10
        risk_flags.append("long_unstructured_text")

    # Numeric/liability handling
    if has_digit and not has_refs:
        score -= 18
        risk_flags.append("numeric_claim_no_evidence")
    if has_digit and has_refs:
        score += 8
        risk_flags.append("numeric_claim_with_evidence")

    # Obvious-fact bump (short, declarative, non-numeric)
    if len(t) < 140 and not has_digit and (" is " in tl or " are " in tl):
        score += 25
        risk_flags.append("short_declarative_claim")

    # NEW: semantic plausibility / domain sanity checks (still MVP-simple)
    plausibility = semantic_plausibility_check(t)

    # plausibility is 0..1 (1 = plausible). Convert to score adjustment.
    # If implausible, penalize; if strongly plausible, small bump.
    if plausibility <= 0.25:
        score -= 22
        risk_flags.append("semantic_implausibility_flag")
    elif plausibility >= 0.85:
        score += 6
        risk_flags.append("semantic_plausibility_support")

    # Clamp
    score = max(0, min(100, score))

    # Verdict
    if score >= 75:
        verdict = "Likely true / consistent"
    elif score >= 55:
        verdict = "Unclear / needs verification"
    else:
        verdict = "High risk of error / hallucination"

    explanation = (
        "MVP heuristic score. This demo evaluates linguistic certainty and uncertainty cues, "
        "basic risk signals, and applies conservative handling for numeric or liability claims "
        "unless evidence is provided. It also includes a lightweight semantic plausibility "
        "check to flag obviously nonsensical statements. Replace with evidence-backed verification "
        "in production."
    )

    signals = {
        "has_references": bool(has_refs),
        "reference_count": len(references),
        "has_digit": bool(has_digit),
        "semantic_plausibility": round(float(plausibility), 2),
        "risk_flags": risk_flags
    }

    return score, verdict, explanation, signals


def semantic_plausibility_check(text: str) -> float:
    """
    Returns a plausibility score from 0.0 to 1.0.
    This is intentionally rule-based (MVP) — no external calls.

    Examples:
    - "Capital of France is Paris" -> high plausibility
    - "There's black candy at the center of every galaxy" -> low plausibility
    """
    t = (text or "").strip()
    tl = t.lower()

    # Very short empty-ish strings are not meaningful
    if len(t) < 6:
        return 0.4

    # "Every galaxy" center claims: allow only a small allowed set
    if "center of every galaxy" in tl or "centre of every galaxy" in tl:
        allowed = [
            "supermassive black hole",
            "black hole",
            "massive black hole",
            "black holes"
        ]
        if any(a in tl for a in allowed):
            return 0.8
        return 0.15

    # Obvious absurd tokens (MVP list)
    absurd_tokens = [
        "black candy",
        "unicorn engine",
        "magic particles",
        "telepathic wifi",
        "invisible rainbow"
    ]
    if any(x in tl for x in absurd_tokens):
        return 0.2

    # Extremely strong universal quantifiers combined with exotic object can be suspicious
    if ("every " in tl or "all " in tl) and ("galaxy" in tl or "planet" in tl or "human" in tl):
        if any(x in tl for x in ["candy", "magic", "telepathy"]):
            return 0.25

    # If it matches a common "X is Y" geography/civics style, treat as likely plausible
    if len(t) < 180 and ("capital of " in tl and " is " in tl):
        return 0.9

    # Default neutral
    return 0.6


if __name__ == "__main__":
    # Local dev only; Render uses gunicorn
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
```0
