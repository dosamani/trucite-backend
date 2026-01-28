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
    # Always ensure we end up with: score, verdict, explanation, signals, references
    if score_claim_text:
        try:
            score, verdict, explanation, signals, references = score_claim_text(
                text, evidence=evidence, policy_mode=policy_mode
            )
            # In case external scorer doesn't provide our needed mitigation fields
            score, verdict, explanation, signals, references = ensure_mvp_mitigations(
                text, evidence, score, verdict, explanation, signals, references
            )
        except TypeError:
            # backwards-compatible signature
            try:
                score, verdict, explanation = score_claim_text(text)
                score, verdict, explanation, signals, references = heuristic_score(text, evidence, seed_score=int(score))
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

    # NEW: High-liability evidence requirement for ALLOW
    # If claim is high-liability and evidence is required but missing -> cap at REVIEW even if score is high
    if signals.get("evidence_required_for_allow") and not signals.get("has_references"):
        # Keep this clearly described for demo credibility
        if score >= 75:
            return "REVIEW", "High-liability output requires evidence to be ALLOWed. Add DOI/PMID/URL to unlock ALLOW."
        # If score isn't ALLOW anyway, proceed with normal thresholds below.

    # Normal thresholds
    if score >= 75:
        return "ALLOW", "High confidence per current MVP scoring."
    elif score >= 55:
        return "REVIEW", "Medium confidence. Human verification recommended."
    else:
        return "BLOCK", "Low confidence. Do not use without verification."


# -------------------------
# Guardrails + helpers
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
    "always", "never", "guaranteed", "definitely", "proves", "proof", "100%", "cures", "cure", "all", "everyone", "no one"
]

# Simple high-liability keyword buckets (MVP demo)
HIGH_LIABILITY_TERMS = [
    # Medical-ish
    "dose", "dosage", "mg", "mcg", "milligram", "contraindication", "diagnosis", "treat", "treatment",
    "cure", "guideline", "clinical", "patient", "drug", "medication", "side effect", "adverse",
    # Legal-ish
    "contract", "legal", "lawsuit", "liability", "compliance", "regulation", "statute", "case law",
    # Finance-ish
    "revenue", "profit", "earnings", "roi", "interest rate", "valuation", "market cap", "stock", "bond"
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


def is_high_liability(text: str) -> bool:
    tl = (text or "").lower()
    if any(term in tl for term in HIGH_LIABILITY_TERMS):
        return True
    # Numeric content often implies finance/medical/statistical assertions
    if has_any_digit(text):
        return True
    return False


def ensure_mvp_mitigations(text: str, evidence: str, score: int, verdict: str, explanation: str,
                           signals: dict, references: list):
    """
    If an external scorer exists, we still enforce TruCite MVP mitigation fields for demo credibility:
    - known_false guardrail behavior
    - universal claim guardrail behavior
    - high-liability evidence requirement to reach ALLOW
    - add liability_tier + evidence_required_for_allow to signals
    """
    # Normalize containers
    signals = signals or {}
    references = references or []

    # Ensure evidence flags exist
    has_refs = bool(signals.get("has_references")) if "has_references" in signals else evidence_present(evidence)
    has_digit = bool(signals.get("has_digit")) if "has_digit" in signals else has_any_digit(text)

    # Build references from evidence if not provided
    if not references:
        ev = (evidence or "").strip()
        for u in extract_urls(ev):
            references.append({"type": "url", "value": u})
        if looks_like_doi_or_pmid(ev) and not extract_urls(ev):
            references.append({"type": "evidence", "value": ev[:240]})

    # Determine liability
    high_liab = is_high_liability(text)
    liability_tier = "high" if high_liab else "low"
    evidence_required_for_allow = True if high_liab else False

    # Apply guardrails similar to heuristic scorer if missing
    guardrail = signals.get("guardrail")
    risk_flags = list(signals.get("risk_flags") or [])

    # Known false without evidence
    if guardrail is None and matches_known_false(text) and not has_refs:
        guardrail = "known_false_claim_no_evidence"
        if "known_false_category_no_evidence" not in risk_flags:
            risk_flags.append("known_false_category_no_evidence")
        # cap score so UI isn't confusing
        score = min(int(score), 45)
        verdict = "High risk of error / hallucination"

    # Universal certainty without evidence: cap ALLOW
    if guardrail is None and is_short_declarative(text) and contains_universal_certainty(text) and not has_refs:
        guardrail = "unsupported_universal_claim_no_evidence"
        if "unsupported_universal_claim_no_evidence" not in risk_flags:
            risk_flags.append("unsupported_universal_claim_no_evidence")
        score = min(int(score), 60)
        if int(score) >= 55:
            verdict = verdict or "Unclear / needs verification"
        else:
            verdict = "High risk of error / hallucination"

    # High liability without evidence: cannot be ALLOW (handled in decision_gate)
    # But for score optics, nudge down a bit if missing evidence
    if high_liab and not has_refs:
        score = min(int(score), 72)  # stays below ALLOW threshold
        if "high_liability_without_evidence" not in risk_flags:
            risk_flags.append("high_liability_without_evidence")

    # Write back normalized signals
    signals["has_references"] = bool(has_refs)
    signals["has_digit"] = bool(has_digit)
    signals["reference_count"] = int(signals.get("reference_count") or len(references))
    signals["risk_flags"] = risk_flags
    signals["guardrail"] = guardrail
    signals["liability_tier"] = liability_tier
    signals["evidence_required_for_allow"] = bool(evidence_required_for_allow)

    # Keep explanation aligned with current messaging
    explanation = explanation or (
        "MVP heuristic score. This demo evaluates linguistic certainty and uncertainty cues, basic risk signals, "
        "and applies conservative handling for numeric or liability claims unless evidence is provided. "
        "It also includes lightweight guardrails to prevent obvious debunked categories and unsupported universal claims "
        "from being ALLOWed without evidence. Replace with evidence-backed verification in production."
    )

    score = max(0, min(100, int(score)))
    return score, verdict, explanation, signals, references


# -------------------------
# MVP heuristic scoring + guardrails (primary fallback)
# -------------------------
def heuristic_score(text: str, evidence: str = "", seed_score: int = 55):
    """
    MVP heuristic scoring (0-100) + conservative guardrails.
    - Scores linguistic certainty/uncertainty + risk signals
    - Evidence boosts only when present (URL/DOI/PMID)
    - High-liability requires evidence to reach ALLOW
    - Guardrails for widely debunked categories + unsupported universal claims without evidence
    """

    t = (text or "")
    tl = t.lower()
    ev = (evidence or "").strip()

    # References extraction (demo only)
    references = []
    for u in extract_urls(ev):
        references.append({"type": "url", "value": u})
    if looks_like_doi_or_pmid(ev) and not extract_urls(ev):
        references.append({"type": "evidence", "value": ev[:240]})

    has_refs = evidence_present(ev)
    has_digit = has_any_digit(t)

    risky_terms = ["always", "never", "guaranteed", "cure", "100%", "proof", "definitely", "everyone", "no one", "all"]
    hedges = ["may", "might", "could", "likely", "possibly", "suggests", "uncertain", "approximately"]

    risk_flags = []
    score = int(seed_score)

    # Liability tier
    high_liab = is_high_liability(t)
    liability_tier = "high" if high_liab else "low"
    evidence_required_for_allow = True if high_liab else False

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

    # Short declarative bump (safe only if not tripping other rules)
    short_decl = is_short_declarative(t)
    if short_decl and not has_digit:
        risk_flags.append("short_declarative_claim")
        score += 20  # modest bump to avoid auto-ALLOW on nonsense

    # --- Guardrail #1: Known false categories (demo list) ---
    if matches_known_false(t) and not has_refs:
        score = min(score, 45)
        risk_flags.append("known_false_category_no_evidence")
        guardrail = "known_false_claim_no_evidence"
    else:
        guardrail = None

    # --- Guardrail #2: Unsupported universal/high-certainty claims w/out evidence ---
    if (short_decl and contains_universal_certainty(t)) and not has_refs and guardrail is None:
        score = min(score, 60)  # prevents ALLOW
        risk_flags.append("unsupported_universal_claim_no_evidence")
        guardrail = "unsupported_universal_claim_no_evidence"

    # --- High-liability without evidence: cap below ALLOW for demo credibility ---
    if high_liab and not has_refs and guardrail is None:
        score = min(score, 72)
        risk_flags.append("high_liability_without_evidence")

    # Evidence helps but doesn't guarantee
    if has_refs:
        score += 5
        risk_flags.append("evidence_present")

    score = max(0, min(100, int(score)))

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
        "guardrail": guardrail,
        "liability_tier": liability_tier,
        "evidence_required_for_allow": bool(evidence_required_for_allow)
    }

    return score, verdict, explanation, signals, references


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
