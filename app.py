import os
import time
import hashlib
import re
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Optional modules (if present in repo)
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

    # Scoring
    if score_claim_text:
        try:
            score, verdict, explanation, signals, references = score_claim_text(text, evidence=evidence, policy_mode=policy_mode)
        except TypeError:
            # backwards-compatible signature
            try:
                score, verdict, explanation = score_claim_text(text)
                score, verdict, explanation, signals, references = enrich_with_guardrails(text, evidence, score, verdict, explanation)
            except Exception:
                score, verdict, explanation, signals, references = heuristic_score(text, evidence)
        except Exception:
            score, verdict, explanation, signals, references = heuristic_score(text, evidence)
    else:
        score, verdict, explanation, signals, references = heuristic_score(text, evidence)

    # Decision Gate
    action, reason = decision_gate(score, signals)

    resp = {
        "verdict": verdict,
        "score": int(score),
        "decision": {"action": action, "reason": reason},
        "event_id": event_id,
        "policy_mode": policy_mode,
        "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},
        "claims": claims,
        "references": references,
        "signals": signals,
        "explanation": explanation
    }

    return jsonify(resp), 200


# -------------------------
# Decision logic
# -------------------------
def decision_gate(score: int, signals: dict):
    # Hard guardrails first
    if signals.get("guardrail") == "known_false_claim_no_evidence":
        return "BLOCK", "Known false / widely debunked category without evidence. Demo guardrail triggered."
    if signals.get("guardrail") == "unsupported_universal_claim_no_evidence":
        return "REVIEW", "Unsupported universal/high-certainty claim without evidence. Conservative gating applied."

    # Normal thresholds
    if score >= 75:
        return "ALLOW", "High confidence per current MVP scoring."
    elif score >= 55:
        return "REVIEW", "Medium confidence. Human verification recommended."
    else:
        return "BLOCK", "Low confidence. Do not use without verification."


# -------------------------
# Guardrails (used if reference_engine not available)
# -------------------------
KNOWN_FALSE_PATTERNS = [
    # Keep this list tiny + obvious for demo credibility
    r"\bthe\s+earth\s+is\s+flat\b",
    r"\bflat\s+earth\b",
    r"\bvaccines?\s+cause\s+autism\b",
    r"\b5g\s+causes?\s+covid\b",
    r"\bmoon\s+landing\s+was\s+fake\b",
]

UNIVERSAL_CERTAINTY_TERMS = [
    "always", "never", "guaranteed", "definitely", "proves", "proof", "100%", "cures", "cure"
]

def has_any_digit(s: str) -> bool:
    return any(ch.isdigit() for ch in (s or ""))

def extract_urls(s: str):
    if not s:
        return []
    return re.findall(r"https?://[^\s)]+", s)

def looks_like_doi_or_pmid(s: str) -> bool:
    if not s:
        return False
    s = s.strip()
    if re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", s, re.I):
        return True
    if re.search(r"\bPMID:\s*\d+\b", s, re.I):
        return True
    if re.search(r"\bpubmed\.ncbi\.nlm\.nih\.gov/\d+\b", s, re.I):
        return True
    return False

def evidence_present(evidence: str) -> bool:
    if not evidence:
        return False
    if extract_urls(evidence):
        return True
    if looks_like_doi_or_pmid(evidence):
        return True
    # fallback: any non-trivial evidence string
    return len(evidence.strip()) >= 12

def is_short_declarative(text: str) -> bool:
    t = (text or "").strip()
    if len(t) > 160:
        return False
    # crude declarative cue
    return (" is " in t.lower()) or (" are " in t.lower()) or t.endswith(".")

def contains_universal_certainty(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in UNIVERSAL_CERTAINTY_TERMS)

def matches_known_false(text: str) -> bool:
    t = (text or "").lower()
    for pat in KNOWN_FALSE_PATTERNS:
        if re.search(pat, t, re.I):
            return True
    return False


def enrich_with_guardrails(text: str, evidence: str, score: int, verdict: str, explanation: str):
    # Used if external scorer is present but not returning signals
    base_score = int(max(0, min(100, score)))
    s, v, e, signals, references = heuristic_score(text, evidence, seed_score=base_score)
    # Keep original verdict/explanation if they are more detailed
    if verdict and isinstance(verdict, str):
        v = verdict
    if explanation and isinstance(explanation, str):
        e = explanation
    return s, v, e, signals, references


# -------------------------
# MVP heuristic scoring + guardrails
# -------------------------
def heuristic_score(text: str, evidence: str = "", seed_score: int = 55):
    """
    MVP heuristic scoring (0-100) + conservative guardrails.
    - Scores linguistic certainty/uncertainty + risk signals
    - Evidence boosts only when present (URL/DOI/PMID)
    - Adds conservative gating for universal/high-certainty unsupported claims
    - Adds tiny demo guardrail list for widely debunked categories (e.g., "Earth is flat")
    """

    t = (text or "")
    tl = t.lower()
    ev = (evidence or "").strip()

    # References extraction (demo only)
    references = []
    for u in extract_urls(ev):
        references.append({"type": "url", "value": u})
    if looks_like_doi_or_pmid(ev) and not extract_urls(ev):
        # capture evidence string as reference if DOI/PMID-like
        references.append({"type": "evidence", "value": ev[:240]})

    has_refs = evidence_present(ev)
    has_digit = has_any_digit(t)

    risky_terms = ["always", "never", "guaranteed", "cure", "100%", "proof", "definitely"]
    hedges = ["may", "might", "could", "likely", "possibly", "suggests", "uncertain"]

    risk_flags = []

    score = int(seed_score)

    if any(w in tl for w in risky_terms):
        score -= 15
        risk_flags.append("high_certainty_language")

    if any(w in tl for w in hedges):
        score += 10
        risk_flags.append("hedging_language")

    if len(t) > 800:
        score -= 10
        risk_flags.append("very_long_output")

    # Numeric / liability: penalize unless evidence
    if has_digit and not has_refs:
        score -= 18
        risk_flags.append("numeric_without_evidence")
    if has_digit and has_refs:
        score += 8
        risk_flags.append("numeric_with_evidence")

    # "Short declarative" bump is ONLY safe if it doesn't trip guardrails
    short_decl = is_short_declarative(t)
    if short_decl and not has_digit:
        risk_flags.append("short_declarative_claim")
        score += 20  # modest bump (not 25) to avoid auto-ALLOW

    # --- Guardrail #1: Known false categories (demo list) ---
    if matches_known_false(t) and not has_refs:
        # Force conservative outcome (no ALLOW)
        score = min(score, 45)
        risk_flags.append("known_false_category_no_evidence")
        guardrail = "known_false_claim_no_evidence"
    else:
        guardrail = None

    # --- Guardrail #2: Unsupported universal/high-certainty claims w/out evidence ---
    # Example: sweeping statements that sound definitive; if no evidence, cap at REVIEW ceiling
    if (short_decl and contains_universal_certainty(t)) and not has_refs and guardrail is None:
        score = min(score, 60)  # prevents ALLOW (>=75)
        risk_flags.append("unsupported_universal_claim_no_evidence")
        guardrail = "unsupported_universal_claim_no_evidence"

    # Evidence helps, but should not automatically guarantee ALLOW
    if has_refs:
        score += 5
        risk_flags.append("evidence_present")

    score = max(0, min(100, score))

    if score >= 75:
        verdict = "Likely true / consistent"
    elif score >= 55:
        verdict = "Unclear / needs verification"
    else:
        verdict = "High risk of error / hallucination"

    explanation = (
        "MVP heuristic score. This demo evaluates linguistic certainty and uncertainty cues, basic risk signals, "
        "and applies conservative handling for numeric or liability claims unless evidence is provided. "
        "It also includes lightweight guardrails to prevent obvious debunked categories and unsupported universal claims "
        "from being ALLOWed without evidence. Replace with evidence-backed verification in production."
    )

    signals = {
        "has_digit": bool(has_digit),
        "has_references": bool(has_refs),
        "reference_count": len(references),
        "risk_flags": risk_flags,
        "guardrail": guardrail
    }

    return score, verdict, explanation, signals, references


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
