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
# Policy metadata (acquirer-facing polish)
# -------------------------
POLICY_VERSION = "2026.01"
POLICY_HASH = hashlib.sha256(POLICY_VERSION.encode("utf-8")).hexdigest()[:12]


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
# Policy endpoint (credibility signal)
# -------------------------
@app.get("/policy")
def policy():
    return jsonify({
        "policy_version": POLICY_VERSION,
        "policy_hash": POLICY_HASH,
        "notes": "MVP policy for scoring + decision gating. Production deployments can replace heuristics with evidence-backed validation.",
        "guardrails": [
            "known_false_claim_no_evidence",
            "unsupported_universal_claim_no_evidence"
        ],
        "decision_thresholds": {
            "low_liability": {"allow": 70, "review": 55},
            "high_liability": {"allow": 75, "review": 55},
            "evidence_required_for_allow_when": "high_liability_or_numeric"
        }
    })


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
                        "200": {
                            "description": "Verification result"
                        }
                    }
                }
            },
            "/verify/batch": {
                "post": {
                    "summary": "Batch verify AI-generated output",
                    "description": "Returns an array of full verification objects for pipeline ingestion.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "items": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "text": {"type": "string"},
                                                    "evidence": {"type": "string"},
                                                    "policy_mode": {"type": "string"}
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Batch verification results"
                        }
                    }
                }
            },
            "/policy": {
                "get": {
                    "summary": "Return current TruCite policy metadata",
                    "description": "Returns policy version/hash and guardrail thresholds for auditing."
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
            # Expecting: score, verdict, explanation, signals, references
            if isinstance(out, (list, tuple)) and len(out) >= 5:
                score, verdict, explanation, signals, references = out[:5]
            elif isinstance(out, (list, tuple)) and len(out) == 3:
                score, verdict, explanation = out
                score, verdict, explanation, signals, references = enrich_with_guardrails(text, evidence, int(score), verdict, explanation)
            else:
                score, verdict, explanation, signals, references = heuristic_score(text, evidence)
        except TypeError:
            # backwards-compatible signature
            try:
                score, verdict, explanation = score_claim_text(text)
                score, verdict, explanation, signals, references = enrich_with_guardrails(text, evidence, int(score), verdict, explanation)
            except Exception:
                score, verdict, explanation, signals, references = heuristic_score(text, evidence)
        except Exception:
            score, verdict, explanation, signals, references = heuristic_score(text, evidence)
    else:
        score, verdict, explanation, signals, references = heuristic_score(text, evidence)

    # Decision Gate (uses signals incl. liability tier + evidence requirement)
    action, reason = decision_gate(int(score), signals)

    # ✅ Ensure rules_fired exists even if a future engine returns signals without it
    if isinstance(signals, dict) and "rules_fired" not in signals:
        signals["rules_fired"] = []

    resp = {
        "verdict": verdict,
        "score": int(score),
        "decision": {"action": action, "reason": reason},
        "event_id": event_id,
        "policy_mode": policy_mode,
        # ✅ policy metadata for auditability
        "policy_version": POLICY_VERSION,
        "policy_hash": POLICY_HASH,
        "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},
        "claims": claims,
        "references": references,
        "signals": signals,
        "explanation": explanation
    }

    return jsonify(resp), 200


# -------------------------
# Batch verify endpoint (Phase-1 polish)
# -------------------------
@app.route("/verify/batch", methods=["POST"])
def verify_batch():
    payload = request.get_json(silent=True) or {}
    items = payload.get("items") or []

    if not isinstance(items, list) or len(items) == 0:
        return jsonify({"error": "Missing 'items' array in request body"}), 400

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue

        text = (item.get("text") or "").strip()
        evidence = (item.get("evidence") or "").strip()
        policy_mode = (item.get("policy_mode") or "enterprise").strip()

        if not text:
            results.append({"error": "Missing 'text' in item"})
            continue

        # Reuse the exact /verify path semantics by internally calling the same logic
        # (We rebuild a response object for each item)
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        event_id = sha[:12]
        ts = datetime.now(timezone.utc).isoformat()

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

        if score_claim_text:
            try:
                out = score_claim_text(text, evidence=evidence, policy_mode=policy_mode)
                if isinstance(out, (list, tuple)) and len(out) >= 5:
                    score, verdict, explanation, signals, references = out[:5]
                elif isinstance(out, (list, tuple)) and len(out) == 3:
                    score, verdict, explanation = out
                    score, verdict, explanation, signals, references = enrich_with_guardrails(text, evidence, int(score), verdict, explanation)
                else:
                    score, verdict, explanation, signals, references = heuristic_score(text, evidence)
            except TypeError:
                try:
                    score, verdict, explanation = score_claim_text(text)
                    score, verdict, explanation, signals, references = enrich_with_guardrails(text, evidence, int(score), verdict, explanation)
                except Exception:
                    score, verdict, explanation, signals, references = heuristic_score(text, evidence)
            except Exception:
                score, verdict, explanation, signals, references = heuristic_score(text, evidence)
        else:
            score, verdict, explanation, signals, references = heuristic_score(text, evidence)

        action, reason = decision_gate(int(score), signals)

        if isinstance(signals, dict) and "rules_fired" not in signals:
            signals["rules_fired"] = []

        results.append({
            "verdict": verdict,
            "score": int(score),
            "decision": {"action": action, "reason": reason},
            "event_id": event_id,
            "policy_mode": policy_mode,
            "policy_version": POLICY_VERSION,
            "policy_hash": POLICY_HASH,
            "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},
            "claims": claims,
            "references": references,
            "signals": signals,
            "explanation": explanation
        })

    return jsonify({
        "policy_version": POLICY_VERSION,
        "policy_hash": POLICY_HASH,
        "count": len(results),
        "results": results
    }), 200


# -------------------------
# Decision logic
# -------------------------
def decision_gate(score: int, signals: dict):
    """
    Demo policy (good for MVP / acqui-hire optics):
      - Known-debunked categories: BLOCK unless evidence
      - High-liability or numeric claims: evidence required to reach ALLOW
      - Low-liability non-numeric: can ALLOW at a lower threshold (e.g., 70)
    """
    guardrail = signals.get("guardrail")
    has_refs = bool(signals.get("has_references"))
    liability = (signals.get("liability_tier") or "low").lower()
    evidence_required_for_allow = bool(signals.get("evidence_required_for_allow"))

    # Hard guardrails first
    if guardrail == "known_false_claim_no_evidence":
        return "BLOCK", "Known false / widely debunked category without evidence. Demo guardrail triggered."

    if guardrail == "unsupported_universal_claim_no_evidence":
        return "REVIEW", "Unsupported universal/high-certainty claim without evidence. Conservative gating applied."

    # Enforce evidence requirement for high-liability or numeric claims
    if evidence_required_for_allow and not has_refs:
        # If it's already low confidence, block instead of wasting human review
        if score < 55:
            return "BLOCK", "Low confidence + no evidence for high-liability or numeric claim. Blocked to prevent downstream harm."

        # Medium or high confidence but missing evidence -> human review
        if score >= 70:
            return "REVIEW", "Likely true, but no evidence provided. Human verification required under high-liability policy."

        return "REVIEW", "No evidence provided for high-liability or numeric claim. Human verification recommended."

    # Thresholds by liability tier
    if liability == "low":
        if score >= 70:
            return "ALLOW", "High confidence per current MVP scoring."
        elif score >= 55:
            return "REVIEW", "Medium confidence. Human verification recommended."
        return "BLOCK", "Low confidence. Do not use without verification."

    else:
        # High-liability tier WITH evidence
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

# Keep tiny + obvious for demo credibility; include variants ("earth is flat" without "the")
KNOWN_FALSE_PATTERNS = [
    r"\bthe\s+earth\s+is\s+flat\b",
    r"\bearth\s+is\s+flat\b",
    r"\bflat\s+earth\b",
    r"\bvaccines?\s+cause\s+autism\b",
    r"\b5g\s+causes?\s+covid\b",
    r"\bmoon\s+landing\s+was\s+fake\b",
]

# High-liability keyword hints (MVP heuristic, not medical/legal advice)
HIGH_LIABILITY_KEYWORDS = [
    # medical
    "dose", "dosage", "mg", "mcg", "units", "diagnosis", "treat", "treatment", "contraindication", "side effect",
    "guideline", "clinical", "patient", "prescribe", "medication", "drug", "insulin", "warfarin",
    # legal
    "contract", "liability", "lawsuit", "indemnify", "breach", "statute", "jurisdiction", "legal advice",
    # finance
    "roi", "interest rate", "apr", "yield", "stock", "market", "earnings", "arr", "revenue", "forecast",
    "valuation", "tax", "irs"
]

def normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    # normalize punctuation/whitespace for stable matching
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
    - Scores linguistic certainty/uncertainty + risk signals
    - Evidence boosts only when present (URL/DOI/PMID)
    - High-liability or numeric claims require evidence to reach ALLOW (enforced in decision_gate)
    - Tiny demo guardrail list for widely debunked categories (e.g., "Earth is flat")
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

    # --- Guardrail #1: Known false categories (demo list) ---
    if matches_known_false(t) and not has_refs:
        score = min(score, 45)
        risk_flags.append("known_false_category_no_evidence")
        rules_fired.append("guardrail_known_false_no_evidence")
        guardrail = "known_false_claim_no_evidence"
    else:
        guardrail = None

    # --- Guardrail #2: Unsupported universal/high-certainty claims w/out evidence ---
    if (short_decl and contains_universal_certainty(t)) and not has_refs and guardrail is None:
        score = min(score, 60)
        risk_flags.append("unsupported_universal_claim_no_evidence")
        rules_fired.append("guardrail_universal_claim_no_evidence")
        guardrail = "unsupported_universal_claim_no_evidence"

    # Evidence helps, but should not auto-guarantee ALLOW
    if has_refs:
        score += 5
        risk_flags.append("evidence_present")
        rules_fired.append("evidence_present_bonus")

    # Slight conservative bias for high-liability without evidence (even before gate)
    if tier == "high" and not has_refs:
        score = min(score, 73)  # prevents "easy" 80+ scores on high-liability w/out evidence
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
        "and applies conservative handling for numeric or liability claims unless evidence is provided. "
        "It also includes lightweight guardrails to prevent obvious debunked categories and unsupported universal claims "
        "from being ALLOWed without evidence. Replace with evidence-backed verification in production."
    )

    signals = {
        "liability_tier": tier,
        "evidence_required_for_allow": bool(evidence_required_for_allow),
        "has_digit": bool(has_digit),
        "has_references": bool(has_refs),
        "reference_count": len(references),
        "risk_flags": risk_flags,
        "rules_fired": rules_fired,
        "guardrail": guardrail
    }

    return score, verdict, explanation, signals, references


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
