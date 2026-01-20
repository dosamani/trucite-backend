# app.py — TruCite backend (MVP) — FastAPI + static landing page

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

app = FastAPI(title="TruCite Engine", version="0.3.1")

# CORS (permissive for MVP demo)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Static site (landing page) ----------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Mount /static if folder exists
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
def home():
    """
    Serve static/index.html as the landing page.
    """
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        # If missing, return a clear error so you know it's a file placement issue
        return HTMLResponse(
            "<h1>TruCite backend is running</h1><p>Missing static/index.html</p>",
            status_code=200,
        )
    return HTMLResponse(index_path.read_text(encoding="utf-8"), status_code=200)

# ---------- API Models ----------
class VerifyRequest(BaseModel):
    text: str
    evidence: Optional[str] = None  # URLs/DOIs/PMIDs/etc

# ---------- Utility / Heuristic Engine ----------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def extract_claims(text: str) -> List[str]:
    """
    Very lightweight claim segmentation for MVP:
    split on newline and sentence-ish boundaries, keep non-empty.
    """
    text = (text or "").strip()
    if not text:
        return []
    # Split on newlines first
    parts = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Further split by sentence boundaries
        segs = re.split(r"(?<=[\.\?\!])\s+", line)
        for s in segs:
            s = s.strip()
            if s:
                parts.append(s)
    return parts[:25]  # cap for MVP

def signal_flags(claim: str, evidence: str = "") -> Dict[str, Any]:
    c = claim.lower()

    has_url = bool(re.search(r"https?://", claim)) or bool(re.search(r"https?://", evidence or ""))
    has_doi = bool(re.search(r"\b10\.\d{4,9}/\S+\b", claim)) or bool(re.search(r"\b10\.\d{4,9}/\S+\b", evidence or ""))
    has_pmid = bool(re.search(r"\bPMID[:\s]*\d+\b", claim, re.IGNORECASE)) or bool(re.search(r"\bPMID[:\s]*\d+\b", evidence or "", re.IGNORECASE))
    has_year = bool(re.search(r"\b(19|20)\d{2}\b", claim))
    has_percent = "%" in claim
    numeric_count = len(re.findall(r"\d+(\.\d+)?", claim))
    has_numerics = numeric_count > 0
    has_citation_like = bool(has_year or has_doi or has_pmid)
    has_sources_provided = bool((evidence or "").strip()) or has_url or has_doi or has_pmid

    absolute_count = len(re.findall(r"\b(always|never|guarantee|proves|definitely|100%)\b", c))
    hedge_count = len(re.findall(r"\b(may|might|could|suggests|possibly|likely|unclear)\b", c))

    return {
        "has_url": has_url,
        "has_doi": has_doi,
        "has_pmid": has_pmid,
        "has_year": has_year,
        "has_percent": has_percent,
        "has_numerics": has_numerics,
        "numeric_count": numeric_count,
        "has_citation_like": has_citation_like,
        "has_sources_provided": has_sources_provided,
        "absolute_count": absolute_count,
        "hedge_count": hedge_count,
    }

def score_claim(claim: str, evidence: str = "") -> Dict[str, Any]:
    sig = signal_flags(claim, evidence)
    risk_tags = []

    # classify claim type
    claim_type = "general_claim"
    if sig["has_numerics"] or sig["has_percent"] or sig["has_citation_like"]:
        claim_type = "numeric_or_stat_claim"

    # baseline score starts higher = more uncertain (worse)
    score = 70

    # absolute language increases risk
    if sig["absolute_count"] > 0:
        score -= 10
        risk_tags.append("absolute_language")

    # numeric/stat claims without sources are higher risk
    if claim_type == "numeric_or_stat_claim":
        risk_tags.append("numeric_claim")
        if not sig["has_sources_provided"]:
            score -= 20
            risk_tags.append("citation_unverified")
        else:
            # if evidence exists, slightly improve (still not "verified" in MVP)
            score += 5

    # if explicit URL/DOI/PMID provided, reduce risk a bit
    if sig["has_url"] or sig["has_doi"] or sig["has_pmid"]:
        score += 5

    # clamp
    score = max(0, min(100, score))

    # verdict
    if score <= 55:
        verdict = "High risk / do not rely"
    elif score <= 75:
        verdict = "Unclear / needs verification"
    else:
        verdict = "Low risk / likely reliable"

    evidence_needed = None
    if claim_type == "numeric_or_stat_claim" and ("citation_unverified" in risk_tags):
        evidence_needed = {
            "required": True,
            "reason": "Claim includes numeric/statistical or citation-like content without an attached source (URL/DOI/PMID) or provided evidence.",
            "acceptable_evidence_examples": [
                "Peer-reviewed paper link (DOI/PMID/URL)",
                "Clinical guideline link (e.g., society guideline URL)",
                "Regulatory label / official statement URL",
                "Internal policy document reference (enterprise mode)",
            ],
            "suggested_query": f"{claim} clinical trial meta-analysis PMID",
        }

    return {
        "text": claim,
        "claim_type": claim_type,
        "signals": sig,
        "risk_tags": list(dict.fromkeys(risk_tags)),
        "score": score,
        "verdict": verdict,
        "evidence_needed": evidence_needed,
    }

# simple in-memory drift per input hash (MVP)
_prior: Dict[str, Dict[str, Any]] = {}

def drift_for_input(input_text: str, score: int, verdict: str) -> Dict[str, Any]:
    key = sha256_hex(input_text.strip().lower())
    prev = _prior.get(key)
    if not prev:
        _prior[key] = {"ts": utc_now_iso(), "score": score, "verdict": verdict, "claim_count": None}
        return {
            "has_prior": False,
            "prior_timestamp_utc": None,
            "score_delta": None,
            "verdict_changed": False,
            "drift_flag": False,
            "claim_count_delta": None,
            "notes": "MVP in-memory drift. Enterprise mode persists histories and compares behavior over time.",
        }
    score_delta = score - prev["score"]
    verdict_changed = verdict != prev["verdict"]
    drift_flag = verdict_changed or abs(score_delta) >= 15
    _prior[key] = {"ts": utc_now_iso(), "score": score, "verdict": verdict, "claim_count": None}
    return {
        "has_prior": True,
        "prior_timestamp_utc": prev["ts"],
        "score_delta": score_delta,
        "verdict_changed": verdict_changed,
        "drift_flag": drift_flag,
        "claim_count_delta": None,
        "notes": "MVP in-memory drift. Enterprise mode persists histories and compares behavior over time.",
    }

# ---------- Routes ----------
@app.get("/health")
def health():
    return {"status": "ok", "service": "trucite-engine", "ts": utc_now_iso()}

@app.post("/verify")
def verify(req: VerifyRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    evidence = (req.evidence or "").strip()
    claims = extract_claims(text)
    if not claims:
        raise HTTPException(status_code=400, detail="no claims detected")

    claim_results = [score_claim(c, evidence) for c in claims]

    # overall score is min of claim scores (most risky dominates)
    overall_score = min([c["score"] for c in claim_results])
    overall_verdict = claim_results[[c["score"] for c in claim_results].index(overall_score)]["verdict"]

    drift = drift_for_input(text, overall_score, overall_verdict)

    event_id = sha256_hex(text)[:12]
    payload = {
        "audit_fingerprint": {"sha256": sha256_hex(text), "timestamp_utc": utc_now_iso()},
        "event_id": event_id,
        "input": {"length_chars": len(text), "num_claims": len(claim_results)},
        "score": overall_score,
        "verdict": overall_verdict,
        "claims": claim_results,
        "drift": drift,
        "uncertainty_map": {"risk_tags": list({t for c in claim_results for t in c["risk_tags"]})},
        "explanation": (
            "MVP heuristic verification. This demo flags risk via claim segmentation, "
            "numeric/stat patterns, citation signals, absolute language, and uncertainty cues. "
            "Enterprise mode adds evidence-backed checks, source validation, and persistent drift analytics."
        ),
    }
    return JSONResponse(payload)

# Helpful: make sure OPTIONS preflight does not surprise you
@app.options("/{rest_of_path:path}")
def options_preflight(rest_of_path: str):
    return JSONResponse({"ok": True})
