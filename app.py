# app.py — TruCite backend (MVP)
# FastAPI app that:
#  - Serves the landing page at "/" from static/index.html
#  - Serves static assets at "/static/*" from ./static
#  - Provides "/health" for uptime checks
#  - Provides "/verify" for scoring
#
# IMPORTANT: This file is defensive about local module function names
# (claim_parser/reference_engine) so Render won't crash on import errors.

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, Dict, Any, List, Callable
from pathlib import Path

import hashlib
import re
from datetime import datetime, timezone

app = FastAPI(title="TruCite Engine", version="0.3.3")

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

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def home():
    """
    Serve static/index.html as the landing page.
    """
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
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
# Dynamic imports (defensive)
# ----------------------------
def _load_claim_extractor() -> Callable[[str], List[Dict[str, Any]]]:
    """
    Returns a function that takes text -> list of claim dicts.
    Works even if claim_parser exports different names.
    """
    try:
        import claim_parser  # type: ignore

        # Most likely names across your iterations
        for fn_name in ("extract_claims", "parse_claims", "split_claims", "get_claims"):
            fn = getattr(claim_parser, fn_name, None)
            if callable(fn):
                return fn  # type: ignore

        # If module has something else, fail gracefully
        def fallback(text: str) -> List[Dict[str, Any]]:
            return [{
                "text": text.strip(),
                "claim_type": "general_claim",
                "signals": {
                    "has_url": bool(re.search(r"https?://", text, re.IGNORECASE)),
                    "has_year": bool(re.search(r"\b(19|20)\d{2}\b", text)),
                    "has_percent": bool(re.search(r"\d+(\.\d+)?\s*%", text)),
                    "has_numerics": bool(re.search(r"\d", text)),
                },
                "risk_tags": ["parser_fallback"],
                "score": 70,
            }]
        return fallback

    except Exception:
        # If import itself fails, don't crash deploy
        def fallback(text: str) -> List[Dict[str, Any]]:
            return [{
                "text": text.strip(),
                "claim_type": "general_claim",
                "signals": {
                    "has_url": bool(re.search(r"https?://", text, re.IGNORECASE)),
                    "has_year": bool(re.search(r"\b(19|20)\d{2}\b", text)),
                    "has_percent": bool(re.search(r"\d+(\.\d+)?\s*%", text)),
                    "has_numerics": bool(re.search(r"\d", text)),
                },
                "risk_tags": ["parser_import_failed"],
                "score": 70,
            }]
        return fallback


def _load_evidence_rescorer() -> Optional[Callable[..., Any]]:
    """
    Optional evidence-aware rescoring.
    """
    try:
        import reference_engine  # type: ignore
        fn = getattr(reference_engine, "evidence_aware_rescore", None)
        if callable(fn):
            return fn  # type: ignore
        return None
    except Exception:
        return None


CLAIM_EXTRACTOR = _load_claim_extractor()
EVIDENCE_RESCORER = _load_evidence_rescorer()

# ----------------------------
# Verify API
# ----------------------------
class VerifyRequest(BaseModel):
    text: str
    evidence: Optional[str] = None  # URLs/DOIs/PMIDs or brief citations


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def detect_url_doi_pmid(evidence_text: str) -> Dict[str, bool]:
    t = evidence_text or ""
    has_url = bool(re.search(r"https?://", t, re.IGNORECASE))
    has_doi = bool(re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", t, re.IGNORECASE))
    has_pmid = bool(re.search(r"\bPMID\s*:?\s*\d+\b", t, re.IGNORECASE)) or bool(re.search(r"\b\d{6,9}\b", t))
    return {"has_url": has_url, "has_doi": has_doi, "has_pmid": has_pmid}


def verdict_from_score(score: int) -> str:
    if score <= 55:
        return "High risk / do not rely"
    if score <= 75:
        return "Unclear / needs verification"
    return "Lower risk / seems supported"


@app.post("/verify")
def verify(payload: VerifyRequest):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing text")

    evidence_text = (payload.evidence or "").strip()
    evidence_flags = detect_url_doi_pmid(evidence_text)

    now = datetime.now(timezone.utc).isoformat()
    audit_sha = sha256_hex(text + "|" + evidence_text + "|" + now)
    event_id = audit_sha[:12]

    claims = CLAIM_EXTRACTOR(text)
    if not isinstance(claims, list):
        claims = [{"text": text, "claim_type": "general_claim", "signals": {}, "risk_tags": ["parser_bad_output"], "score": 70}]

    scored_claims: List[Dict[str, Any]] = []
    for c in claims:
        claim_text = str(c.get("text", "")).strip() or text
        claim_type = str(c.get("claim_type", "general_claim"))
        signals = c.get("signals", {}) or {}

        try:
            score = int(c.get("score", 70))
        except Exception:
            score = 70

        risk_tags = list(c.get("risk_tags", [])) if isinstance(c.get("risk_tags", []), list) else []

        if claim_type in ("numeric_or_stat_claim", "medical_claim", "legal_claim"):
            if not (evidence_flags["has_url"] or evidence_flags["has_doi"] or evidence_flags["has_pmid"]):
                score = min(score, 55)
                if "citation_unverified" not in risk_tags:
                    risk_tags.append("citation_unverified")
                if claim_type == "numeric_or_stat_claim" and "numeric_claim" not in risk_tags:
                    risk_tags.append("numeric_claim")

        verdict = verdict_from_score(score)

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

        scored_claims.append({
            "text": claim_text,
            "claim_type": claim_type,
            "signals": signals,
            "risk_tags": risk_tags,
            "score": score,
            "verdict": verdict,
            "evidence_needed": evidence_needed,
        })

    min_score = min(c["score"] for c in scored_claims) if scored_claims else 70
    avg_score = (sum(c["score"] for c in scored_claims) / len(scored_claims)) if scored_claims else 70
    overall = int(round((min_score * 0.6) + (avg_score * 0.4)))
    overall_verdict = verdict_from_score(overall)

    if EVIDENCE_RESCORER and evidence_text and (evidence_flags["has_url"] or evidence_flags["has_doi"] or evidence_flags["has_pmid"]):
        try:
            scored_claims, overall, overall_verdict = EVIDENCE_RESCORER(
                scored_claims=scored_claims,
                evidence_text=evidence_text,
                current_overall=overall,
                current_verdict=overall_verdict,
            )
        except Exception:
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
