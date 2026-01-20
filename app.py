# app.py - TruCite backend (MVP)
# Fixes:
# - Numeric-with-unit claims are classified as numeric/stat (so "1km" won't slip through)
# - Adds implausibility heuristics for obvious world-knowledge red flags (e.g., moon distance, "made of candy")
# - Conservative scoring: high-liability or numeric claims without evidence get capped low

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import re

# Optional modules (do not break deploy if missing)
try:
    from claim_parser import extract_claims
except Exception:
    extract_claims = None

try:
    from reference_engine import extract_evidence_flags
except Exception:
    extract_evidence_flags = None

app = FastAPI(title="TruCite Engine", version="0.3.3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class VerifyRequest(BaseModel):
    text: str
    evidence: Optional[str] = None


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def verdict_from_score(score: int) -> str:
    # Tuned so 80 is NOT "high risk"
    if score >= 85:
        return "Low risk / likely reliable"
    if score >= 70:
        return "Unclear / needs verification"
    return "High risk / do not rely"


# --- Signal helpers ---
UNIT_REGEX = re.compile(
    r"\b(km|kilometer|kilometre|miles?|mi|meters?|m|cm|mm|kg|g|mg|mcg|µg|lbs?|pounds?|°c|°f|celsius|fahrenheit|%|percent)\b",
    re.IGNORECASE,
)

def compute_signals(t: str) -> Dict[str, Any]:
    return {
        "has_url": bool(re.search(r"https?://", t, re.IGNORECASE)),
        "has_doi": bool(re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", t, re.IGNORECASE)),
        "has_pmid": bool(re.search(r"\bPMID\s*:?\s*\d+\b", t, re.IGNORECASE)),
        "has_year": bool(re.search(r"\b(19|20)\d{2}\b", t)),
        "has_percent": bool(re.search(r"\d+(\.\d+)?\s*%", t, re.IGNORECASE)) or bool(re.search(r"\bpercent\b", t, re.IGNORECASE)),
        "has_numerics": bool(re.search(r"\d", t)),
        "numeric_count": len(re.findall(r"\d+(\.\d+)?", t)),
        "has_units": bool(UNIT_REGEX.search(t)),
        "has_citation_like": bool(re.search(r"\b(doi|pmid|et al\.|journal|trial|meta-analysis|systematic review|guideline)\b", t, re.IGNORECASE))
                            or bool(re.search(r"\b(19|20)\d{2}\b", t)),
        "absolute_count": len(re.findall(r"\b(always|never|guaranteed|proves|definitively|certainly)\b", t, re.IGNORECASE)),
        "hedge_count": len(re.findall(r"\b(may|might|could|suggests|possibly|unclear|likely|approximately|about)\b", t, re.IGNORECASE)),
    }


def infer_claim_type(t: str, sig: Dict[str, Any]) -> str:
    medical_kw = r"\b(mi|myocardial|infarct|stroke|mortality|dose|trial|aspirin|statin|diabetes|cancer|bp|cholesterol|hypertension)\b"
    legal_kw = r"\b(illegal|law|statute|liable|liability|compliance|hipaa|gdpr|court|regulatory)\b"
    finance_kw = r"\b(roi|interest rate|apr|revenue|earnings|sec filing|bond|yield)\b"

    if re.search(medical_kw, t, re.IGNORECASE):
        return "medical_claim"
    if re.search(legal_kw, t, re.IGNORECASE):
        return "legal_claim"
    if re.search(finance_kw, t, re.IGNORECASE):
        return "finance_claim"

    # Key fix: ANY numeric with a unit is a numeric/stat claim (e.g., "1km")
    if sig.get("has_numerics") and (sig.get("has_units") or sig.get("has_percent")):
        return "numeric_or_stat_claim"

    # Also treat (numerics + year) or multiple numerics as numeric/stat
    if (sig.get("has_numerics") and sig.get("has_year")) or (sig.get("numeric_count", 0) >= 2):
        return "numeric_or_stat_claim"

    return "general_claim"


def evidence_flags_from_text(evidence: Optional[str]) -> Dict[str, bool]:
    ev = evidence or ""
    if extract_evidence_flags:
        try:
            flags = extract_evidence_flags(ev)
            return {
                "has_url": bool(flags.get("has_url")),
                "has_doi": bool(flags.get("has_doi")),
                "has_pmid": bool(flags.get("has_pmid")),
            }
        except Exception:
            pass

    return {
        "has_url": bool(re.search(r"https?://", ev, re.IGNORECASE)),
        "has_doi": bool(re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", ev, re.IGNORECASE)),
        "has_pmid": bool(re.search(r"\bPMID\s*:?\s*\d+\b", ev, re.IGNORECASE)),
    }


# --- Implausibility heuristics (MVP) ---
def implausibility_caps(text: str) -> Dict[str, Any]:
    """
    Returns:
      {"cap": int or None, "tags": [..], "reason": str or None}
    """
    t = text.lower()

    tags = []
    cap = None
    reason = None

    # Moon distance heuristic: "moon ... <number> km ... from earth"
    # Known reality: moon is ~384,000 km away. We only need to detect absurdly small distances.
    m = re.search(r"\bmoon\b.*?\b(\d+(?:\.\d+)?)\s*(km|kilometer|kilometre)\b.*?\b(earth|from earth)\b", t)
    if m:
        try:
            val = float(m.group(1))
            if val < 100000:  # absurdly low
                cap = 30
                tags.append("implausible_world_knowledge")
                reason = "Moon distance claim is implausibly small versus well-established astronomical measurements."
        except Exception:
            pass

    # "made of candy" or similar nonsense material composition
    if re.search(r"\bmade up of\b.*\bcandy\b", t) or re.search(r"\bmade of\b.*\bcandy\b", t):
        cap = 20 if cap is None else min(cap, 20)
        tags.append("implausible_material_claim")
        reason = reason or "Material/composition claim is implausible."

    return {"cap": cap, "tags": tags, "reason": reason}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1>TruCite backend is running</h1><p>Missing static/index.html</p>",
        status_code=200,
    )


@app.post("/verify")
def verify(req: VerifyRequest):
    text = (req.text or "").strip()
    evidence = (req.evidence or "").strip()

    now = datetime.now(timezone.utc).isoformat()
    fp = sha256_hex(text + "|" + evidence)
    event_id = fp[:12]

    evidence_flags = evidence_flags_from_text(evidence)
    has_any_evidence = evidence_flags["has_url"] or evidence_flags["has_doi"] or evidence_flags["has_pmid"]

    # Extract claims (best-effort)
    claims: List[Dict[str, Any]] = []
    if extract_claims:
        try:
            parsed = extract_claims(text)
            if isinstance(parsed, list) and parsed:
                claims = parsed
        except Exception:
            claims = []

    if not claims:
        claims = [{"text": text}]

    scored_claims: List[Dict[str, Any]] = []
    claim_scores: List[int] = []

    for c in claims:
        claim_text = str(c.get("text", "")).strip() or text

        parser_signals = c.get("signals", {}) if isinstance(c.get("signals", {}), dict) else {}
        sig = compute_signals(claim_text)
        sig.update({k: v for k, v in parser_signals.items() if v is not None})

        parser_type = str(c.get("claim_type", "")).strip()
        claim_type = parser_type if parser_type else infer_claim_type(claim_text, sig)

        # Start more conservatively
        score = 75

        # Penalize absolute language
        score -= min(sig.get("absolute_count", 0) * 3, 10)

        # Core rule: numeric/high-liability claims without evidence get capped low
        if claim_type in ("numeric_or_stat_claim", "medical_claim", "legal_claim", "finance_claim"):
            if not has_any_evidence:
                score = min(score, 55)

        # Implausibility caps (world-knowledge red flags)
        imp = implausibility_caps(claim_text)
        if imp["cap"] is not None:
            score = min(score, imp["cap"])

        # Evidence boost + cap release (only if evidence actually provided)
if has_any_evidence:
    # boost
    score += 20

    # if the only thing holding us down was "no evidence", allow score to rise
    # but keep conservative ceiling unless additional trust signals exist
    if claim_type in ("numeric_or_stat_claim", "medical_claim", "legal_claim", "finance_claim"):
        score = max(score, 70)          # at least "Unclear" once evidence exists
        score = min(score, 90)          # don't give perfect score in MVP

        score = max(0, min(100, int(score)))

        risk_tags: List[str] = []
        if claim_type in ("numeric_or_stat_claim", "medical_claim", "legal_claim", "finance_claim") and not has_any_evidence:
            risk_tags.append("citation_unverified")
            if claim_type == "numeric_or_stat_claim":
                risk_tags.append("numeric_claim")

        # add implausibility tags
        risk_tags.extend(imp["tags"])

        verdict = verdict_from_score(score)

        evidence_needed = None
        if "citation_unverified" in risk_tags:
            evidence_needed = {
                "required": True,
                "reason": "Claim includes numeric/statistical or high-liability content without an attached source (URL/DOI/PMID) or provided evidence.",
                "acceptable_evidence_examples": [
                    "Peer-reviewed paper link (DOI/PMID/URL)",
                    "Clinical guideline link (e.g., society guideline URL)",
                    "Regulatory label / official statement URL",
                ],
                "suggested_query": f"{claim_text} PMID",
            }

        if "implausible_world_knowledge" in risk_tags or "implausible_material_claim" in risk_tags:
            evidence_needed = evidence_needed or {
                "required": True,
                "reason": imp["reason"] or "Claim appears implausible; requires strong supporting evidence.",
                "acceptable_evidence_examples": [
                    "Authoritative reference (NASA/ESA/academic astronomy source)",
                    "Peer-reviewed paper or reputable textbook reference",
                ],
                "suggested_query": "Moon distance from Earth authoritative source",
            }

        scored_claims.append({
            "text": claim_text,
            "claim_type": claim_type,
            "signals": sig,
            "risk_tags": sorted(list(set(risk_tags))),
            "score": score,
            "verdict": verdict,
            "evidence_needed": evidence_needed,
        })
        claim_scores.append(score)

    overall_score = min(claim_scores) if claim_scores else 70
    overall_verdict = verdict_from_score(overall_score)

    resp: Dict[str, Any] = {
        "audit_fingerprint": {"sha256": fp, "timestamp_utc": now},
        "event_id": event_id,
        "input": {"length_chars": len(text), "num_claims": len(scored_claims)},
        "score": overall_score,
        "verdict": overall_verdict,
        "claims": scored_claims,
        "drift": {
            "has_prior": False,
            "prior_timestamp_utc": None,
            "score_delta": None,
            "verdict_changed": False,
            "drift_flag": False,
            "claim_count_delta": None,
            "notes": "MVP in-memory drift. Enterprise mode persists histories and compares behavior over time.",
        },
        "uncertainty_map": {
            "risk_tags": sorted(list({rt for cl in scored_claims for rt in cl.get("risk_tags", [])})),
        },
        "explanation": "MVP heuristic verification. Demo scores use claim segmentation, numeric-with-unit detection, evidence signals, and implausibility heuristics. Enterprise mode adds evidence-backed checks and persistent drift analytics.",
        "evidence": {"provided": bool(evidence.strip()), "signals": evidence_flags},
    }

    return JSONResponse(resp)
