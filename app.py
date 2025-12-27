from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import hashlib
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse
import re
import os

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

# -----------------------------
# Config
# -----------------------------
ENGINE_VERSION = "TruCite Claim Engine v2.3 (MVP)"

# Allowlist: trusted reference domains only (Wikipedia blocked)
ALLOWED_REFERENCE_DOMAINS = {
    "cdc.gov",
    "www.cdc.gov",
    "nih.gov",
    "www.nih.gov",
    "nasa.gov",
    "www.nasa.gov",
    "who.int",
    "www.who.int",
    "fda.gov",
    "www.fda.gov",
    "cms.gov",
    "www.cms.gov",
    "noaa.gov",
    "www.noaa.gov",
    "whitehouse.gov",
    "www.whitehouse.gov",
    "sec.gov",
    "www.sec.gov",
    "nature.com",
    "www.nature.com",
    "science.org",
    "www.science.org",
}

BLOCKED_REFERENCE_DOMAINS = {
    "wikipedia.org",
    "www.wikipedia.org",
    "en.wikipedia.org",
}

REFERENCE_LINE_PREFIXES = ("source:", "sources:", "reference:", "references:", "citation:", "citations:")

# -----------------------------
# Routes
# -----------------------------
@app.get("/")
def serve_index():
    return send_from_directory("static", "index.html")

@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.post("/truth-score")
def truth_score():
    payload = request.get_json(silent=True) or {}
    raw_text = (payload.get("text") or "").strip()

    if not raw_text:
        return jsonify({"error": "Missing 'text'"}), 400

    # -----------------------------
    # Step 2 FIX: normalize newlines
    # Handles real \n, \r\n, and escaped \\n
    # -----------------------------
    normalized = raw_text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")

    # Extract references first, and remove reference lines from claim text
    references = []
    kept_lines = []

    for line in normalized.split("\n"):
        stripped = line.strip()
        low = stripped.lower()

        # If the line starts with "Source:" etc, treat rest of line as ref text
        if any(low.startswith(pfx) for pfx in REFERENCE_LINE_PREFIXES):
            ref_part = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            refs_found = extract_urls(ref_part)
            references.extend(refs_found)
            continue

        # Otherwise, scan line for URLs too (inline refs)
        inline_refs = extract_urls(stripped)
        if inline_refs:
            references.extend(inline_refs)

        kept_lines.append(stripped)

    # Deduplicate & enforce allowlist + blocklist
    filtered_refs = []
    seen = set()

    for r in references:
        dom = r["domain"]
        if dom in BLOCKED_REFERENCE_DOMAINS:
            continue
        # allowlist enforcement: also permit base domain match
        base = ".".join(dom.split(".")[-2:]) if len(dom.split(".")) >= 2 else dom
        if (dom not in ALLOWED_REFERENCE_DOMAINS) and (base not in ALLOWED_REFERENCE_DOMAINS):
            continue

        key = (dom, r["url"])
        if key in seen:
            continue
        seen.add(key)
        filtered_refs.append(r)

    # Build a clean claim text (without reference lines)
    clean_text = " ".join([l for l in kept_lines if l]).strip()

    # Claim extraction (MVP: 1 claim if any text remains)
    claims = []
    if clean_text:
        claims.append({
            "id": "c1",
            "type": "factual",
            "text": clean_text,
            "confidence_weight": 1
        })

    # Score heuristic (simple MVP)
    score = score_heuristic(clean_text, filtered_refs)

    verdict = verdict_from_score(score)

    event_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()

    audit = {
        "engine_version": ENGINE_VERSION,
        "hash": sha256_hex(normalized),
        "timestamp_utc": ts
    }

    response = {
        "score": score,
        "verdict": verdict,
        "claims": claims,
        "references": filtered_refs,
        "reference_note": "Reference grounding in allowlist mode: only trusted domains are permitted. Wikipedia blocked.",
        "risk_summary": risk_summary(score, filtered_refs),
        "trust_profile": trust_profile(score),
        "event_id": event_id,
        "audit_fingerprint": audit,
        "explanation": "MVP mode: returning a baseline score plus extracted claims. Next steps will add reference-grounding and drift tracking."
    }

    return jsonify(response)

# -----------------------------
# Helpers
# -----------------------------
URL_REGEX = re.compile(r"(https?://[^\s\)\]\}<>\"']+)", re.IGNORECASE)

def extract_urls(text: str):
    if not text:
        return []
    urls = URL_REGEX.findall(text)
    results = []
    for u in urls:
        try:
            p = urlparse(u)
            dom = (p.netloc or "").lower().strip()
            if dom:
                results.append({"domain": dom, "url": u})
        except:
            continue
    return results

def sha256_hex(s: str):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def score_heuristic(text: str, references: list):
    # baseline
    score = 72

    low = (text or "").lower()

    # obvious absurdity triggers
    absurd_terms = ["made of candy", "made of cheese", "moon is 1km", "1km from earth", "flat earth"]
    if any(t in low for t in absurd_terms):
        score -= 18

    # numeric oddities
    if "1km" in low or "1 km" in low:
        score -= 10

    # if user attaches a serious domain to nonsense, increase penalty
    if references and any("cdc.gov" in r["domain"] or "nih.gov" in r["domain"] for r in references) and ("candy" in low or "cheese" in low or "1km" in low):
        score -= 8

    # clamp
    return max(0, min(100, int(round(score))))

def verdict_from_score(score: int):
    if score >= 85:
        return "Likely True / Well-Supported"
    if score >= 65:
        return "Plausible / Needs Verification"
    if score >= 40:
        return "Questionable / High Uncertainty"
    return "Likely False / Misleading"

def risk_summary(score: int, references: list):
    if score >= 70:
        return {"misinformation_risk": "Medium", "model_confidence_gap": "Moderate", "regulatory_exposure": "Medium"}
    if score >= 40:
        return {"misinformation_risk": "High", "model_confidence_gap": "Significant", "regulatory_exposure": "Medium"}
    return {"misinformation_risk": "High", "model_confidence_gap": "Significant", "regulatory_exposure": "High"}

def trust_profile(score: int):
    s = max(0.0, min(1.0, score / 100.0))
    # simple consistent outputs (MVP placeholders)
    return {
        "reliability": round(s, 2),
        "grounding_strength": round(max(0.05, s - 0.07), 2),
        "drift_risk": round(min(0.95, 1.0 - s + 0.15), 2),
        "volatility": round(min(0.95, 1.0 - s + 0.10), 2)
    }

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
