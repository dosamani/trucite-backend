# app.py — TruCite backend (MVP)
# FastAPI app that:
#  - Serves the landing page at "/" from static/index.html
#  - Serves static assets at "/static/*" from ./static
#  - Provides "/health" for uptime checks
#  - Provides "/verify" for scoring

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from pathlib import Path

# Local modules (these must exist in repo root)
from claim_parser import extract_claims  # type: ignore
from reference_engine import evidence_aware_rescore  # type: ignore

import hashlib
import re
from datetime import datetime, timezone

app = FastAPI(title="TruCite Engine", version="0.3.2")

# CORS: permissive for MVP demo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Static site (landing page)
# ----------------------------
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
        # Clear error if file missing
        return HTMLResponse(
            "<h1>TruCite backend is running</h1>"
            "<p>Missing <code>static/index.html</code>. Add it to your repo.</p>",
            status_code=200,
        )
    return HTMLResponse(index_path.read_text(encoding="utf-8"), status_code=200)


# ----------------------------
# Health
# ----------------------------
@app.get("/health")
def health():
    return JSONResponse({"status": "ok"})


# ----------------------------
# Verify API
# ----------------------------
class VerifyRequest(BaseModel):
    text: str
    evidence: Optional[str] = None  # URLs/DOIs/PMIDs or brief citations


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def detect_url_doi_pmid(evidence_text: str) -> Dict[str, bool]:
    """
    Lightweight detectors for evidence strings.
    """
    t = evidence_text or ""
    has_url = bool(re.search(r"https?://", t, re.IGNORECASE))
    has_doi = bool(re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", t, re.IGNORECASE))
    has_pmid = bool(re.search(r"\bPMID\s*:\s*\d+\b|\bPMID\s*\d+\b|\b\d{6,9}\b", t, re.IGNORECASE))
    return {"has_url": has_url, "has_doi": has_doi, "has_pmid": has_pmid}


@app.post("/verify")
def verify(payload: VerifyRequest):
    """
    MVP heuristic verifier.
    - Splits claims
    - Scores risk using heuristic rules
    - If evidence is provided (URL/DOI/PMID), applies evidence-aware rescore
    """
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing text")

    evidence_text = (payload.evidence or "").strip()
    evidence_flags = detect_url_doi_pmid(evidence_text)

    # 1) Extract claims
    claims = extract_claims(text)

    # 2) Build response structure
    now = datetime.now(timezone.utc).isoformat()
    audit_sha = sha256_hex(text + "|" + evidence_text + "|" + now)
    event_id = audit_sha[:12]

    # 3) Score each claim (heuristic baseline)
    scored_claims: List[Dict[str, Any]] = []
    for c in claims:
        claim_text = c.get("text", "")
        claim_type = c.get("claim_type", "general_claim")
        signals = c.get("signals", {}) or {}

        # baseline score from parser
        score = int(c.get("score", 70))
        risk_tags = list(c.get("risk_tags", []))

        # If numeric/stat claim without evidence: penalize
        if claim_type in ("numeric_or_stat_claim", "medical_claim", "legal_claim"):
            if not (evidence_flags["has_url"] or evidence_flags["has_doi"] or evidence_flags["has_pmid"]):
                score = min(score, 55)
                if "citation_unverified" not in risk_tags:
                    risk_tags.append("citation_unverified")
                if "numeric_claim" not in risk_tags and claim_type == "numeric_or_stat_claim":
                    risk_tags.append("numeric_claim")

        # Verdict mapping
        if score <= 55:
            verdict = "High risk / do not rely"
        elif score <= 75:
            verdict = "Unclear / needs verification"
        else:
            verdict = "Lower risk / seems supported"

        evidence_needed = None
        if "citation_unverified" in risk_tags:
            evidence_needed = {
                "required": True,
                "reason": "Claim includes numeric/statistical or citation-like content without an attached source (URL/DOI/PMID) or provided evidence.",
                "acceptable_evidence_examples": [
                    "Peer-reviewed paper link (DOI/PMID/URL)",
                    "Clinical guideline link (e.g., society guideline URL)",
                    "Regulatory label / official statement URL",
                    "Internal policy document reference (enterprise mode)",
                ],
                "suggested_query": f"{claim_text} PMID",
            }

        scored_claims.append(
            {
                "text": claim_text,
                "claim_type": claim_type,
                "signals": signals,
                "risk_tags": risk_tags,
                "score": score,
                "verdict": verdict,
                "evidence_needed": evidence_needed,
            }
        )

    # 4) Overall score = min/avg blend (conservative)
    if scored_claims:
        min_score = min(c["score"] for c in scored_claims)
        avg_score = sum(c["score"] for c in scored_claims) / len(scored_claims)
        overall = int(round((min_score * 0.6) + (avg_score * 0.4)))
    else:
        overall = 70

    if overall <= 55:
        overall_verdict = "High risk / do not rely"
    elif overall <= 75:
        overall_verdict = "Unclear / needs verification"
    else:
        overall_verdict = "Lower risk / seems supported"

    # 5) Evidence-aware rescore (only if evidence provided)
    # This function should reduce risk if evidence contains DOI/PMID/URL.
    if evidence_text and (evidence_flags["has_url"] or evidence_flags["has_doi"] or evidence_flags["has_pmid"]):
        try:
            scored_claims, overall, overall_verdict = evidence_aware_rescore(
                scored_claims=scored_claims,
                evidence_text=evidence_text,
                current_overall=overall,
                current_verdict=overall_verdict,
            )
        except Exception:
            # Never crash MVP verify if evidence module fails
            pass

    response = {
        "audit_fingerprint": {
            "sha256": audit_sha,
            "timestamp_utc": now,
        },
        "event_id": event_id,
        "input": {
            "length_chars": len(text),
            "num_claims": len(scored_claims),
        },
        "score": overall,
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
            "risk_tags": sorted(list({t for c in scored_claims for t in c.get("risk_tags", [])})),
        },
        "explanation": (
            "MVP heuristic verification. This demo flags risk via claim segmentation, numeric/stat patterns, "
            "citation signals, absolute language, and uncertainty cues. Enterprise mode adds evidence-backed checks, "
            "source validation, and persistent drift analytics."
        ),
        "evidence": {
            "provided": bool(evidence_text),
            "signals": evidence_flags,
        },
    }

    return JSONResponse(response)
