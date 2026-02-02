import os
import time
import hashlib
import re
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory, make_response
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
# OpenAPI stub (API credibility signal)
# -------------------------
@app.get("/openapi.json")
def openapi_spec():
    return jsonify({
        "openapi": "3.0.0",
        "info": {
            "title": "TruCite Verification API",
            "version": "1.0.0",
            "description": "Independent AI output verification and decision gating layer."
        },
        "paths": {
            "/verify": {
                "post": {
                    "summary": "Verify AI-generated output",
                    "description": "Returns reliability score and enforcement decision for AI output.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                        "evidence": {"type": "string"},
                                        "policy_mode": {"type": "string"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Verification result"}
                    }
                }
            }
        }
    })


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
    # If reference_engine exists and returns signals/references, we’ll use it.
    # If it returns only (score, verdict, explanation), we enrich with our guardrails.
    if score_claim_text:
        try:
            out = score_claim_text(text, evidence=evidence, policy_mode=policy_mode)
            if isinstance(out, (list, tuple)) and len(out) >= 5:
                score, verdict, explanation, signals, references = out[:5]
            elif isinstance(out, (list, tuple)) and len(out) == 3:
                score, verdict, explanation = out
                score, verdict, explanation, signals, references = enrich_with_guardrails(
                    text, evidence, int(score), verdict, explanation
                )
            else:
                score, verdict, explanation, signals, references = heuristic_score(text, evidence)
        except TypeError:
            # backwards-compatible signature
            try:
                score, verdict, explanation = score_claim_text(text)
                score, verdict, explanation, signals, references = enrich_with_guardrails(
                    text, evidence, int(score), verdict, explanation
                )
            except Exception:
                score, verdict, explanation, signals, references = heuristic_score(text, evidence)
        except Exception:
            score, verdict, explanation, signals, references = heuristic_score(text, evidence)
    else:
        score, verdict, explanation, signals, references = heuristic_score(text, evidence)

    # Decision Gate (uses signals incl. liability tier + evidence requirement + volatility)
    action, reason = decision_gate(int(score), signals)

    resp = {
        "verdict": verdict,
        "score": int(score),
        "decision": {"action": action, "reason": reason},
        "event_id": event_id,
        "policy_mode": policy_mode,
        "policy_version": "2026.02",
        "policy_hash": policy_hash(policy_mode),
        "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},
        "claims": claims,
        "references": references,
        "signals": signals,
        "explanation": explanation
    }

    return jsonify(resp), 200
    # -------------------------
# Policy hashing (audit signal)
# -------------------------
def policy_hash(policy_mode: str) -> str:
    seed = f"trucite|{(policy_mode or 'enterprise').strip().lower()}|2026.02"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


# -------------------------
# Decision logic (UPDATED for volatility + legal)
# -------------------------
def decision_gate(score: int, signals: dict):
    """
    Demo policy (good for MVP / acquisition optics):
      - Known-debunked categories: BLOCK unless evidence
      - High-liability or numeric claims: evidence required to reach ALLOW
      - Volatile knowledge (current events / leadership / legal-regulatory): evidence required to reach ALLOW
      - Low-liability non-numeric: can ALLOW at a lower threshold (e.g., 70)
    """
    guardrail = signals.get("guardrail")
    has_refs = bool(signals.get("has_references"))
    liability = (signals.get("liability_tier") or "low").lower()
    evidence_required_for_allow = bool(signals.get("evidence_required_for_allow"))

    # NEW: volatility gating signals
    volatile = bool(signals.get("volatile_knowledge"))
    volatile_class = (signals.get("volatile_class") or "").strip()  # e.g., "current_events", "legal_regulatory"
    volatility_requires_evidence = bool(signals.get("volatility_requires_evidence"))

    # Hard guardrails first
    if guardrail == "known_false_claim_no_evidence":
        return "BLOCK", "Known false / widely debunked category without evidence. Demo guardrail triggered."

    if guardrail == "unsupported_universal_claim_no_evidence":
        return "REVIEW", "Unsupported universal/high-certainty claim without evidence. Conservative gating applied."

    # NEW: Volatility requires evidence to reach ALLOW
    if volatile and volatility_requires_evidence and not has_refs:
        # This prevents embarrassing ALLOW decisions on current office-holders / legal status / regulations
        if score >= 75:
            return "REVIEW", f"High confidence signal, but '{volatile_class}' is time-variant / jurisdiction-dependent. Evidence required to ALLOW."
        if score >= 55:
            return "REVIEW", f"'{volatile_class}' claims are volatile. Evidence required. Human verification recommended."
        return "BLOCK", f"Low confidence + no evidence for volatile '{volatile_class}' claim. Blocked to prevent downstream misuse."

    # Enforce evidence for ALLOW in high-liability (or numeric) cases
    if evidence_required_for_allow and not has_refs:
        if score >= 70:
            return "REVIEW", "Likely true, but no evidence provided. Conservative demo policy requires human verification."
        if score >= 45:
            return "REVIEW", "No evidence provided for high-liability or numeric claim. Human verification recommended."
        return "BLOCK", "Low confidence + no evidence for high-liability or numeric claim. Blocked to prevent downstream harm."

    # Thresholds by liability tier
    if liability == "low":
        if score >= 70:
            return "ALLOW", "High confidence per current MVP scoring."
        elif score >= 55:
            return "REVIEW", "Medium confidence. Human verification recommended."
        return "BLOCK", "Low confidence. Do not use without verification."
    else:
        if score >= 75:
            return "ALLOW", "High confidence with evidence under high-liability policy."
        elif score >= 55:
            return "REVIEW", "Medium confidence. Human verification recommended."
        return "BLOCK", "Low confidence. Do not use without verification."
        # -------------------------
# Guardrails + parsing helpers
# -------------------------
UNIVERSAL_CERTAINTY_TERMS = [
    "always", "never", "guaranteed", "definitely", "proves", "proof", "100%", "cures", "cure", "no doubt"
]

KNOWN_FALSE_PATTERNS = [
    r"\bthe\s+earth\s+is\s+flat\b",
    r"\bearth\s+is\s+flat\b",
    r"\bflat\s+earth\b",
    r"\bvaccines?\s+cause\s+autism\b",
    r"\b5g\s+causes?\s+covid\b",
    r"\bmoon\s+landing\s+was\s+fake\b",
]

HIGH_LIABILITY_KEYWORDS = [
    # medical
    "dose", "dosage", "mg", "mcg", "units", "diagnosis", "treat", "treatment", "contraindication", "side effect",
    "guideline", "clinical", "patient", "prescribe", "medication", "drug", "insulin", "warfarin",
    # legal
    "contract", "liability", "lawsuit", "indemnify", "breach", "statute", "jurisdiction", "legal advice",
    "compliance", "regulation", "regulatory", "gdpr", "hipaa", "hitech",
    # finance
    "roi", "interest rate", "apr", "yield", "stock", "market", "earnings", "arr", "revenue", "forecast",
    "valuation", "tax", "irs"
]

# NEW: volatility triggers (time-variant + jurisdiction-dependent)
CURRENT_EVENTS_TERMS = [
    "current", "currently", "as of", "today", "now", "this year", "this month", "recently"
]

OFFICE_HOLDER_TERMS = [
    "prime minister", "president", "chancellor", "governor", "senator", "speaker",
    "ceo", "cfo", "cto", "chairman", "chair", "head of"
]

LEGAL_VOLATILITY_TERMS = [
    "legal", "illegal", "lawful", "unlawful",
    "statute", "case law", "precedent", "ruling", "court held", "supreme court",
    "regulation", "regulatory", "compliance", "gdpr", "hipaa", "hitech",
    "requires", "prohibits", "mandatory", "permitted",
    "jurisdiction", "in california", "in texas", "in new york", "in the eu", "in india", "in canada"
]


def normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^\w\s%./:-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


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
    return len(evidence.strip()) >= 12


def is_short_declarative(text: str) -> bool:
    t = (text or "").strip()
    if len(t) > 160:
        return False
    tl = normalize_text(t)
    return (" is " in tl) or (" are " in tl) or t.endswith(".")


def contains_universal_certainty(text: str) -> bool:
    tl = normalize_text(text)
    return any(w in tl for w in UNIVERSAL_CERTAINTY_TERMS)


def matches_known_false(text: str) -> bool:
    tl = normalize_text(text)
    for pat in KNOWN_FALSE_PATTERNS:
        if re.search(pat, tl, re.I):
            return True
    return False


def liability_tier(text: str) -> str:
    tl = normalize_text(text)
    if has_any_digit(text):
        return "high"
    for kw in HIGH_LIABILITY_KEYWORDS:
        if kw in tl:
            return "high"
    return "low"


# NEW: volatility detector (current events + legal/regulatory)
def detect_volatility(text: str) -> dict:
    tl = normalize_text(text)

    # current events / office-holder volatility
    office_holder = any(term in tl for term in OFFICE_HOLDER_TERMS)
    currentness = any(term in tl for term in CURRENT_EVENTS_TERMS)

    # legal/regulatory volatility
    legalish = any(term in tl for term in LEGAL_VOLATILITY_TERMS)

    volatile = False
    vclass = None

    # If it asserts office-holder OR explicitly uses "current" phrasing => volatile
    if office_holder or currentness:
        volatile = True
        vclass = "current_events"

    # If it claims legality/compliance/regulatory status => volatile (jurisdiction-dependent)
    if legalish:
        volatile = True
        # if both trigger, keep the sharper class label
        vclass = "legal_regulatory" if vclass is None else f"{vclass}+legal_regulatory"

    return {
        "volatile_knowledge": volatile,
        "volatile_class": vclass,
        "volatility_requires_evidence": volatile  # for MVP, always require evidence for volatile class to ALLOW
    }


def enrich_with_guardrails(text: str, evidence: str, score: int, verdict: str, explanation: str):
    base_score = int(max(0, min(100, score)))
    s, v, e, signals, references = heuristic_score(text, evidence, seed_score=base_score)
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
    Adds:
      - Volatile knowledge detection (current events + legal/regulatory) => evidence required to ALLOW
    """

    raw = (text or "")
    t = raw.strip()
    tl = normalize_text(t)
    ev = (evidence or "").strip()

    # References extraction (demo only)
    references = []
    for u in extract_urls(ev):
        references.append({"type": "url", "value": u})
    if looks_like_doi_or_pmid(ev) and not extract_urls(ev):
        references.append({"type": "evidence", "value": ev[:240]})

    has_refs = evidence_present(ev)
    has_digit = has_any_digit(t)

    tier = liability_tier(t)
    evidence_required_for_allow = (tier == "high")

    # NEW: volatility flags
    volatility = detect_volatility(t)
    volatile_knowledge = bool(volatility.get("volatile_knowledge"))
    volatile_class = volatility.get("volatile_class")
    volatility_requires_evidence = bool(volatility.get("volatility_requires_evidence"))

    risky_terms = ["always", "never", "guaranteed", "cure", "100%", "proof", "definitely", "no doubt"]
    hedges = ["may", "might", "could", "likely", "possibly", "suggests", "uncertain"]

    risk_flags = []
    rules_fired = []
    score = int(seed_score)

    # certainty / hedging signals
    if any(w in tl for w in risky_terms):
        score -= 15
        risk_flags.append("high_certainty_language")
        rules_fired.append("high_certainty_language_penalty")

    if any(w in tl for w in hedges):
        score += 10
        risk_flags.append("hedging_language")
        rules_fired.append("hedging_language_bonus")

    if len(t) > 800:
        score -= 10
        risk_flags.append("very_long_output")
        rules_fired.append("very_long_output_penalty")

    # Numeric / liability: penalize unless evidence
    if has_digit and not has_refs:
        score -= 18
        risk_flags.append("numeric_without_evidence")
        rules_fired.append("numeric_without_evidence_penalty")
    if has_digit and has_refs:
        score += 8
        risk_flags.append("numeric_with_evidence")
        rules_fired.append("numeric_with_evidence_bonus")

    short_decl = is_short_declarative(t)
    if short_decl and not has_digit:
        risk_flags.append("short_declarative_claim")
        score += 18
        rules_fired.append("short_declarative_bonus")

    # Guardrail #1: Known false categories (demo list)
    if matches_known_false(t) and not has_refs:
        score = min(score, 45)
        risk_flags.append("known_false_category_no_evidence")
        rules_fired.append("known_false_category_cap")
        guardrail = "known_false_claim_no_evidence"
    else:
        guardrail = None

    # Guardrail #2: Unsupported universal/high-certainty claims w/out evidence
    if (short_decl and contains_universal_certainty(t)) and not has_refs and guardrail is None:
        score = min(score, 60)
        risk_flags.append("unsupported_universal_claim_no_evidence")
        rules_fired.append("unsupported_universal_claim_cap")
        guardrail = "unsupported_universal_claim_no_evidence"

    # NEW: Volatility cap without evidence (prevents accidental ALLOW on office-holder / legal claims)
    if volatile_knowledge and volatility_requires_evidence and not has_refs:
        score = min(score, 68)  # forces REVIEW at best unless evidence is provided
        risk_flags.append("volatile_knowledge_without_evidence_cap")
        rules_fired.append("volatile_knowledge_without_evidence_cap")

    # Evidence helps, but should not auto-guarantee ALLOW
    if has_refs:
        score += 5
        risk_flags.append("evidence_present")
        rules_fired.append("evidence_present_bonus")

    # Conservative bias for high-liability without evidence
    if tier == "high" and not has_refs:
        score = min(score, 73)
        risk_flags.append("high_liability_without_evidence_cap")
        rules_fired.append("high_liability_without_evidence_cap")

    score = max(0, min(100, int(score)))

    if score >= 75:
        verdict = "Likely true / consistent"
    elif score >= 55:
        verdict = "Unclear / needs verification"
    else:
        verdict = "High risk of error / hallucination"

    explanation = (
        "MVP heuristic score. This demo evaluates linguistic certainty and uncertainty cues, basic risk signals, "
        "and applies conservative handling for numeric, high-liability, and volatile knowledge claims unless evidence is provided. "
        "It also includes lightweight guardrails to prevent obvious debunked categories and unsupported universal claims "
        "from being ALLOWed without evidence. Replace with evidence-backed verification in production."
    )

    signals = {
        "liability_tier": tier,
        "evidence_required_for_allow": bool(evidence_required_for_allow),
        "has_digit": bool(has_digit),
        "has_references": bool(has_refs),
        "reference_count": len(references),

        # NEW volatility signals (important for enterprise credibility)
        "volatile_knowledge": volatile_knowledge,
        "volatile_class": volatile_class,
        "volatility_requires_evidence": bool(volatility_requires_evidence),

        "risk_flags": risk_flags,
        "rules_fired": rules_fired,
        "guardrail": guardrail
    }

    return score, verdict, explanation, signals, references


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
