# app.py
# TruCite Backend (Render) — single-file Flask app
# - Serves the landing page + static assets
# - POST /truth-score returns MVP scoring payload
# - FIXED: "Source:" / "Sources:" / "Reference(s):" / "Citation(s):" lines are parsed into references[]
#          and DO NOT become factual claims.

import os
import re
import uuid
import hashlib
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory

# -----------------------------
# App + static hosting
# -----------------------------
app = Flask(__name__, static_folder="static", static_url_path="/static")

# Serve landing page
@app.get("/")
def index():
    # expects: /static/index.html
    return send_from_directory(app.static_folder, "index.html")

# Health check
@app.get("/health")
def health():
    return jsonify({"ok": True})

# -----------------------------
# Configuration: references
# -----------------------------
# Allowlist domains (edit freely)
ALLOWLIST_DOMAINS = {
    "cdc.gov",
    "www.cdc.gov",
    "nih.gov",
    "www.nih.gov",
    "ncbi.nlm.nih.gov",
    "who.int",
    "www.who.int",
    "fda.gov",
    "www.fda.gov",
    "cms.gov",
    "www.cms.gov",
    "sec.gov",
    "www.sec.gov",
    "ftc.gov",
    "www.ftc.gov",
    "nasa.gov",
    "www.nasa.gov",
    "esa.int",
    "www.esa.int",
    "data.gov",
    "www.data.gov",
    "justice.gov",
    "www.justice.gov",
    "gov.uk",
    "www.gov.uk",
    "europa.eu",
    "www.europa.eu",
}

# Blocklist domains (hard block)
BLOCKLIST_DOMAINS = {
    "wikipedia.org",
    "www.wikipedia.org",
    "en.wikipedia.org",
    "m.wikipedia.org",
    "wikimedia.org",
    "www.wikimedia.org",
}

# -----------------------------
# Helpers
# -----------------------------
URL_RE = re.compile(r"https?://[^\s\)\]\}<>\"']+", re.IGNORECASE)

SOURCE_LINE_RE = re.compile(
    r"^\s*(source|sources|reference|references|citation|citations)\s*:\s*",
    re.IGNORECASE,
)

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def extract_urls(text: str):
    return URL_RE.findall(text or "")

def domain_from_url(url: str) -> str:
    # minimal domain extraction without external libs
    # https://www.cdc.gov/path -> www.cdc.gov
    m = re.match(r"^https?://([^/]+)", url.strip(), re.IGNORECASE)
    return (m.group(1).lower() if m else "")

def is_blocked_domain(domain: str) -> bool:
    return domain in BLOCKLIST_DOMAINS or domain.endswith(".wikipedia.org") or domain.endswith(".wikimedia.org")

def is_allowed_domain(domain: str) -> bool:
    if not domain:
        return False
    if is_blocked_domain(domain):
        return False
    if domain in ALLOWLIST_DOMAINS:
        return True
    # allow *.gov and *.edu by default (conservative, but practical)
    if domain.endswith(".gov") or domain.endswith(".edu"):
        return True
    return False

def normalize_text_for_hash(text: str) -> str:
    # stable hashing even if whitespace changes
    t = (text or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t

def verdict_from_score(score: int) -> str:
    if score >= 85:
        return "Likely True / Well-Supported"
    if score >= 65:
        return "Plausible / Needs Verification"
    if score >= 40:
        return "Questionable / High Uncertainty"
    return "Likely False / Misleading"

def risk_summary_from_score(score: int):
    # coarse MVP mapping
    if score >= 80:
        return {
            "misinformation_risk": "Low",
            "model_confidence_gap": "Low",
            "regulatory_exposure": "Low",
        }
    if score >= 60:
        return {
            "misinformation_risk": "Medium",
            "model_confidence_gap": "Moderate",
            "regulatory_exposure": "Medium",
        }
    if score >= 40:
        return {
            "misinformation_risk": "High",
            "model_confidence_gap": "Significant",
            "regulatory_exposure": "Medium",
        }
    return {
        "misinformation_risk": "High",
        "model_confidence_gap": "Severe",
        "regulatory_exposure": "High",
    }

def trust_profile_from_score(score: int, references_count: int):
    # simple derived metrics (MVP placeholders, but consistent)
    # reliability tracks score; grounding_strength bumps with references_count
    reliability = max(0.0, min(1.0, score / 100.0))
    grounding_strength = max(0.0, min(1.0, 0.25 + 0.12 * references_count + 0.45 * reliability))
    volatility = max(0.0, min(1.0, 0.15 + (1.0 - reliability) * 0.65))
    drift_risk = max(0.0, min(1.0, 0.20 + (1.0 - reliability) * 0.75))
    return {
        "reliability": round(reliability, 2),
        "grounding_strength": round(grounding_strength, 2),
        "volatility": round(volatility, 2),
        "drift_risk": round(drift_risk, 2),
    }

def extract_claims_and_references(raw_text: str):
    """
    FIXED BEHAVIOR:
    - Lines starting with Source(s)/Reference(s)/Citation(s): are treated as citation lines.
      URLs in those lines are extracted into references[].
      Those lines do NOT become claims.
    - URLs elsewhere are still extracted into references[] (allowlist mode).
    - Claims are MVP: 1 claim = remaining text (cleaned), unless empty.
    """
    text = raw_text or ""
    lines = [ln.rstrip() for ln in text.splitlines()]
    references = []
    kept_lines = []

    for ln in lines:
        if SOURCE_LINE_RE.match(ln):
            # citation line: extract URLs, do not keep as claim text
            for u in extract_urls(ln):
                references.append(u)
            continue
        kept_lines.append(ln)

    cleaned_text = "\n".join(kept_lines).strip()

    # also extract URLs from remaining text
    for u in extract_urls(cleaned_text):
        references.append(u)

    # de-duplicate URLs preserving order
    seen = set()
    deduped_urls = []
    for u in references:
        if u not in seen:
            seen.add(u)
            deduped_urls.append(u)

    # Apply allowlist / blocklist
    allowed_refs = []
    blocked_any = False
    for u in deduped_urls:
        d = domain_from_url(u)
        if is_blocked_domain(d):
            blocked_any = True
            continue
        if is_allowed_domain(d):
            allowed_refs.append({"domain": d, "url": u})

    # Claims: MVP single factual claim from cleaned_text, but remove any trailing "Source:" fragments if present
    claims = []
    if cleaned_text:
        claims.append({
            "id": "c1",
            "type": "factual",
            "text": cleaned_text,
            "confidence_weight": 1
        })

    reference_note = None
    if allowed_refs:
        reference_note = "Reference grounding in allowlist mode: only trusted domains are permitted. Wikipedia blocked."
    else:
        if blocked_any:
            reference_note = "Reference grounding not enabled in MVP. Wikipedia blocked as a reference source."
        else:
            reference_note = "Reference grounding not enabled in MVP. Wikipedia blocked as a reference source."

    return claims, allowed_refs, reference_note

def score_mvp(claims, references):
    """
    MVP scoring heuristic (NOT true grounding yet).
    - Penalize obviously extreme/implausible patterns (tiny distance to moon, candy, etc.)
    - Give a modest bump if allowed references exist (still not verifying content).
    """
    base = 72
    text = (claims[0]["text"] if claims else "").lower()

    # quick penalties for common nonsense markers
    penalties = 0
    if "made of candy" in text or "made of cheese" in text:
        penalties += 18
    if "1km" in text or "1 km" in text:
        penalties += 18
    if "moon is" in text and ("1km" in text or "made of" in text):
        penalties += 6

    # slight bump for having allowlisted references (still not grounding)
    bump = 0
    if references:
        bump += 4

    score = base - penalties + bump
    score = int(max(0, min(100, score)))
    return score

# -----------------------------
# API: truth score
# -----------------------------
@app.post("/truth-score")
def truth_score():
    payload = request.get_json(silent=True) or {}
    raw_text = (payload.get("text") or "").strip()

    if not raw_text:
        return jsonify({"error": "Missing 'text' in request body."}), 400

    claims, references, reference_note = extract_claims_and_references(raw_text)

    score = score_mvp(claims, references)
    verdict = verdict_from_score(score)

    norm = normalize_text_for_hash(raw_text)
    out = {
        "score": score,
        "verdict": verdict,
        "explanation": "MVP mode: returning a baseline score plus extracted claims. Next steps will add reference-grounding and drift tracking.",
        "claims": claims,
        "references": references,
        "reference_note": reference_note,
        "risk_summary": risk_summary_from_score(score),
        "trust_profile": trust_profile_from_score(score, len(references)),
        "event_id": str(uuid.uuid4()),
        "audit_fingerprint": {
            "engine_version": "TruCite Claim Engine v2.2 (MVP)",
            "hash": sha256_hex(norm),
            "timestamp_utc": utc_now_iso(),
        },
    }
    return jsonify(out)

# -----------------------------
# Optional: run locally
# -----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
```0
