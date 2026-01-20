# app.py - TruCite backend (MVP)
# FastAPI app that:
# 1) Serves static landing page (static/index.html) at "/"
# 2) Exposes /verify endpoint that scores text + optional evidence
# 3) Applies consistent heuristic downgrade for numeric/stat + high-liability claims without evidence

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

# --- Optional imports (do not fail deploy if missing) ---
try:
    from claim_parser import extract_claims  # expected to return list[dict] with "text" optionally
except Exception:
    extract_claims = None

try:
    from reference_engine import extract_evidence_flags  # optional helper
except Exception:
    extract_evidence_flags = None

app = FastAPI(title="TruCite Engine", version="0.3.2")

# CORS (permissive for MVP demo)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Static site mount ---
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
    if score >= 85:
        return "Low risk / likely reliable"
    if score >= 70:
        return "Unclear / needs verification"
    return "High risk / do not rely"


def compute_signals(t: str) -> Dict[str, Any]:
    return {
        "has_url": bool(re.search(r"https?://", t, re.IGNORECASE)),
        "has_doi": bool(re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", t, re.IGNORECASE)),
        "has_pmid": bool(re.search(r"\bPMID\s*:?\s*\d+\b", t, re.IGNORECASE)),
        "has_year": bool(re.search(r"\b(19|20)\d{2}\b", t)),
        "has_percent": bool(re.search(r"\d+(\.\d+)?\s*%", t)),
        "has_numerics": bool(re.search(r"\d", t)),
        "numeric_count": len(re.findall(r"\d+(\.\d+)?", t)),
        "has_citation_like": bool(re.search(r"\b(doi|pmid|et al\.|journal|trial|meta-analysis)\b", t, re.IGNORECASE))
                            or bool(re.search(r"\b(19|20)\d{2}\b", t)),
        "absolute_count": len(re.findall(r"\b(always|never|guaranteed|proves|definitively|certainly)\b", t, re.IGNORECASE)),
        "hedge_count": len(re.findall(r"\b(may|might|could|suggests|possibly|unclear|likely|approximately)\b", t, re.IGNORECASE)),
    }


def infer_claim_type(t: str, sig: Dict[str, Any]) -> str:
    # Minimal, consistent MVP inference
    medical_kw = r"\b(mi|myocardial|infarct|stroke|mortality|dose|trial|aspirin|statin|diabetes|cancer|bp|cholesterol|hypertension)\b"
    legal_kw = r"\b(illegal|law|statute|liable|liability|compliance|hipaa|gdpr|court|regulatory)\b"
    finance_kw = r"\b(roi|interest rate|apr|revenue|earnings|sec filing|bond|yield)\b"

    if re.search(medical_kw, t, re.IGNORECASE):
        return "medical_claim"
    if re.search(legal_kw, t, re.IGNORECASE):
        return "legal_claim"
    if re.search(finance_kw, t, re.IGNORECASE):
        return "finance_claim"

    # Numeric/stat claim if percent OR (any numerics + year) OR multiple numerics
    if sig.get("has_percent") or (sig.get("has_numerics") and sig.get("has_year")) or (sig.get("numeric_count", 0) >= 2):
        return "numeric_or_stat_claim"

    return "general_claim"


def evidence_flags_from_text(evidence: Optional[str]) -> Dict[str, bool]:
    ev = evidence or ""
    if extract_evidence_flags:
        try:
            flags = extract_evidence_flags(ev)
            # normalize
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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home():
    """
    Serve static/index.html as the landing page if present,
    otherwise show a minimal status page.
    """
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

    # 1) Extract claims (best-effort)
    claims: List[Dict[str, Any]] = []
    if extract_claims:
        try:
            parsed = extract_claims(text)
            if isinstance(parsed, list) and parsed:
                claims = parsed
        except Exception:
            claims = []

    # Fallback: treat entire input as one claim
    if not claims:
        claims = [{"text": text}]

    scored_claims: List[Dict[str, Any]] = []
    claim_scores: List[int] = []

    for c in claims:
        claim_text = str(c.get("text", "")).strip() or text

        # Always compute signals; optionally merge parser-provided ones
        parser_signals = c.get("signals", {}) if isinstance(c.get("signals", {}), dict) else {}
        sig = compute_signals(claim_text)
        sig.update({k: v for k, v in parser_signals.items() if v is not None})

        # Determine claim type
        parser_type = str(c.get("claim_type", "")).strip()
        claim_type = parser_type if parser_type else infer_claim_type(claim_text, sig)

        # Base score heuristic:
        # Start at 80, then adjust down for risk patterns; adjust up for strong evidence presence.
        score = 80

        # Penalize absolute language slightly
        score -= min(sig.get("absolute_count", 0) * 3, 10)

        # Penalize numerics without evidence (core rule)
        if claim_type in ("numeric_or_stat_claim", "medical_claim", "legal_claim", "finance_claim"):
            if not has_any_evidence:
                score = min(score, 55)

        # If explicit evidence is provided, modestly boost
        if has_any_evidence:
            score += 8

        # Clamp
        score = max(0, min(100, int(score)))

        risk_tags: List[str] = []
        if claim_type in ("numeric_or_stat_claim", "medical_claim", "legal_claim", "finance_claim") and not has_any_evidence:
            risk_tags.extend(["citation_unverified"])
            if claim_type == "numeric_or_stat_claim":
                risk_tags.append("numeric_claim")

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

        scored_claims.append({
            "text": claim_text,
            "claim_type": claim_type,
            "signals": sig,
            "risk_tags": risk_tags,
            "score": score,
            "verdict": verdict,
            "evidence_needed": evidence_needed,
        })
        claim_scores.append(score)

    # Overall score: minimum claim score (conservative for liability domains)
    overall_score = min(claim_scores) if claim_scores else 70
    overall_verdict = verdict_from_score(overall_score)

    resp: Dict[str, Any] = {
        "audit_fingerprint": {
            "sha256": fp,
            "timestamp_utc": now,
        },
        "event_id": event_id,
        "input": {
            "length_chars": len(text),
            "num_claims": len(scored_claims),
        },
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
        "explanation": "MVP heuristic verification. Demo scores use claim segmentation, numeric/stat patterns, evidence signals, and conservative liability rules. Enterprise mode adds evidence-backed checks and persistent drift analytics.",
        "evidence": {
            "provided": bool(evidence.strip()),
            "signals": evidence_flags,
        },
    }

    return JSONResponse(resp)
