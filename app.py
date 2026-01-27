import os
import time
import hashlib
import re
from urllib.parse import urlparse
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# If you have these modules in your repo, we’ll use them.
# If not, we’ll fallback gracefully.
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
    # Serve static/index.html
    return send_from_directory(app.static_folder, "index.html")


# (Optional but helpful) explicitly serve your static assets
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
# Evidence + flags helpers
# -------------------------
def parse_evidence(evidence: str):
    """
    Parse evidence lines for:
    - URLs
    - DOI (10.xxxx/xxxxx)
    - PMID (PMID: ########) or bare digits (6-10 chars)
    Returns list of reference objects (format-only validation).
    """
    raw = (evidence or "").strip()
    if not raw:
        return []

    refs = []
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]

    doi_re = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
    pmid_re = re.compile(r"\bPMID[:\s]*([0-9]{6,10})\b", re.IGNORECASE)

    for ln in lines:
        # URL
        if ln.startswith("http://") or ln.startswith("https://"):
            try:
                p = urlparse(ln)
                host = p.netloc
                ok = bool(p.scheme and p.netloc)
            except Exception:
                host = ""
                ok = False

            refs.append({
                "type": "url",
                "value": ln,
                "source_host": host,
                "status": "valid_format" if ok else "invalid_format"
            })
            continue

        # DOI
        doi_m = doi_re.search(ln)
        if doi_m:
            refs.append({
                "type": "doi",
                "value": doi_m.group(0),
                "status": "valid_format"
            })
            continue

        # PMID (explicit)
        pmid_m = pmid_re.search(ln)
        if pmid_m:
            refs.append({
                "type": "pmid",
                "value": pmid_m.group(1),
                "status": "valid_format"
            })
            continue

        # Bare digits -> PMID candidate
        if ln.isdigit() and 6 <= len(ln) <= 10:
            refs.append({
                "type": "pmid",
                "value": ln,
                "status": "valid_format"
            })
            continue

        # Unknown line
        refs.append({
            "type": "unknown",
            "value": ln,
            "status": "unverified"
        })

    # De-dupe
    seen = set()
    out = []
    for r in refs:
        key = (r["type"], r["value"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def compute_flags(text: str, evidence: str):
    t = (text or "").lower()
    ev = (evidence or "").strip()
    flags = []

    risky = ["always", "never", "guaranteed", "cure", "100%", "proof", "definitely"]
    if any(w in t for w in risky):
        flags.append("absolute_claim_language")

    has_digit = any(ch.isdigit() for ch in (text or ""))
    if has_digit and not ev:
        flags.append("numeric_claim_without_evidence")

    if len(text or "") > 800:
        flags.append("long_form_output_risk")

    # citation-like patterns in claim itself (not evidence box)
    if "doi:" in t or "pmid" in t or "http" in t:
        flags.append("citation_like_pattern_in_claim")

    return flags


# -------------------------
# Verify endpoint (MUST allow POST)
# -------------------------
@app.route("/verify", methods=["POST", "OPTIONS"])
def verify():
    # Handle preflight (Render + browsers)
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
            # normalize to list of {"text": "..."}
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

    # Scoring (fallback to MVP heuristic if reference_engine not available)
    score_breakdown = []
    if score_claim_text:
        try:
            out = score_claim_text(text)
            # expected: (score, verdict, explanation)
            score, verdict, explanation = out[0], out[1], out[2]
        except Exception:
            out = heuristic_score(text, evidence)
            score, verdict, explanation = out[0], out[1], out[2]
            score_breakdown = out[3] if len(out) > 3 else []
    else:
        out = heuristic_score(text, evidence)
        score, verdict, explanation = out[0], out[1], out[2]
        score_breakdown = out[3] if len(out) > 3 else []

    # Decision Gate (ALWAYS included so frontend is consistent)
    if score >= 75:
        action = "ALLOW"
        reason = "High confidence per current MVP scoring."
    elif score >= 55:
        action = "REVIEW"
        reason = "Medium confidence. Human verification recommended."
    else:
        action = "BLOCK"
        reason = "Low confidence. Do not use without verification."

    # Evidence parsing + flags (NEW)
    references = parse_evidence(evidence)
    flags = compute_flags(text, evidence)

    resp = {
        "verdict": verdict,
        "score": int(score),

        # kept consistent with your current frontend
        "decision": {"action": action, "reason": reason, "policy_mode": policy_mode},

        "event_id": event_id,
        "audit_fingerprint": {
            "sha256": sha,
            "timestamp_utc": ts
        },
        "input": {
            "length_chars": len(text),
            "num_claims": len(claims),
            "policy_mode": policy_mode
        },

        "claims": claims,
        "explanation": explanation,

        # ✅ new fields for the "Validation details, explanation & references" panel
        "evidence": {
            "provided": bool(evidence.strip()),
            "raw": evidence,
            "references": references
        },
        "flags": flags,
        "score_breakdown": score_breakdown
    }

    return jsonify(resp), 200


def heuristic_score(text: str, evidence: str = ""):
    """
    Simple MVP heuristic scoring (0-100).
    - Keeps your original behavior as baseline
    - Adds a small "obvious fact" bump for short, non-numeric declarative claims
    - Penalizes numeric/liability claims unless evidence is provided
    Returns: (score, verdict, explanation, breakdown)
    """
    t = (text or "").lower()
    ev = (evidence or "").strip()

    risky = ["always", "never", "guaranteed", "cure", "100%", "proof", "definitely"]
    hedges = ["may", "might", "could", "likely", "possibly", "suggests", "uncertain"]

    base = 55
    score = base
    breakdown = [{"signal": "base", "delta": base, "reason": "default baseline"}]

    if any(w in t for w in risky):
        score -= 15
        breakdown.append({"signal": "absolute_language", "delta": -15, "reason": "risky absolutes present"})

    if any(w in t for w in hedges):
        score += 10
        breakdown.append({"signal": "hedging_language", "delta": +10, "reason": "uncertainty/hedging detected"})

    # Very long / rambly text tends to be lower confidence
    if len(text) > 800:
        score -= 10
        breakdown.append({"signal": "length_penalty", "delta": -10, "reason": "very long text"})

    # Numeric/liability: penalize unless evidence is provided
    has_digit = any(ch.isdigit() for ch in (text or ""))
    if has_digit and not ev:
        score -= 18
        breakdown.append({"signal": "numeric_no_evidence", "delta": -18, "reason": "digits present without evidence"})
    if has_digit and ev:
        score += 8
        breakdown.append({"signal": "numeric_with_evidence", "delta": +8, "reason": "digits present with evidence"})

    # "Obvious fact" bump (short, declarative, non-numeric)
    if len(text) < 140 and not has_digit:
        if " is " in t or " are " in t:
            score += 25
            breakdown.append({"signal": "short_declarative_bump", "delta": +25, "reason": "short declarative statement"})

    score = max(0, min(100, score))

    if score >= 75:
        verdict = "Likely true / consistent"
    elif score >= 55:
        verdict = "Unclear / needs verification"
    else:
        verdict = "High risk of error / hallucination"

    explanation = (
        "MVP heuristic score. This demo evaluates linguistic certainty/uncertainty cues, "
        "basic risk signals, and applies conservative handling for numeric/liability claims "
        "unless evidence is provided. Replace with evidence-backed verification in production."
    )

    return score, verdict, explanation, breakdown


if __name__ == "__main__":
    # local dev only; Render uses gunicorn
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
```0
