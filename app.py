# app.py — TruCite backend (MVP)
# FastAPI + static landing page + /verify scoring endpoint

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import re

# Optional claim parser (do NOT fail deploy if missing)
extract_claims = None
try:
    from claim_parser import extract_claims as _extract_claims  # type: ignore
    extract_claims = _extract_claims
except Exception:
    extract_claims = None


app = FastAPI(title="TruCite Engine", version="0.3.3")

# CORS (permissive for MVP demo)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Static site hosting
# ----------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
def home():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse(
            content=(
                "<h2>TruCite backend is running</h2>"
                "<p><strong>Missing static/index.html</strong> in repo.</p>"
            ),
            status_code=200,
        )
    return HTMLResponse(index_path.read_text(encoding="utf-8"), status_code=200)

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "TruCite Backend",
        "ts_utc": datetime.now(timezone.utc).isoformat(),
    }

# ----------------------------
# Models
# ----------------------------
class VerifyRequest(BaseModel):
    text: str
    evidence: Optional[str] = None  # URLs/DOIs/PMIDs pasted from evidence box

# ----------------------------
# Helpers: signals + scoring
# ----------------------------
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
PMID_RE = re.compile(r"\bPMID\s*:\s*\d+\b|\bPMID\s+\d+\b|\b\d{6,9}\b", re.IGNORECASE)

ABSOLUTE_WORDS = [
    "always", "never", "guaranteed", "proven", "definitely", "certainly",
    "no doubt", "cannot", "will", "must", "only", "everyone", "nobody"
]

def compute_signals(text: str) -> Dict[str, Any]:
    t = text.strip()
    has_url = bool(URL_RE.search(t))
    has_doi = bool(DOI_RE.search(t))
    has_pmid = bool(PMID_RE.search(t))
    has_year = bool(re.search(r"\b(19|20)\d{2}\b", t))
    has_percent = "%" in t
    numerics = re.findall(r"\d+(\.\d+)?", t)
    numeric_count = len(numerics)
    has_numerics = numeric_count > 0

    absolute_count = 0
    lower = t.lower()
    for w in ABSOLUTE_WORDS:
        absolute_count += lower.count(w)

    has_citation_like = bool(
        re.search(r"\[\d+\]|\(\s*(19|20)\d{2}\s*\)", t)
        or (has_year and has_percent)
    )

    return {
        "has_url": has_url,
        "has_doi": has_doi,
        "has_pmid": has_pmid,
        "has_year": has_year,
        "has_percent": has_percent,
        "has_numerics": has_numerics,
        "numeric_count": numeric_count,
        "absolute_count": absolute_count,
        "has_citation_like": has_citation_like,
    }

def infer_claim_type(text: str, sig: Dict[str, Any]) -> str:
    lower = text.lower()

    if sig.get("has_percent") or sig.get("numeric_count", 0) >= 2:
        return "numeric_or_stat_claim"

    med_words = ["aspirin", "mi", "myocardial", "stroke", "mortality", "risk", "trial"]
    if any(w in lower for w in med_words):
        return "medical_claim"

    fin_words = ["roi", "yield", "interest rate", "stock", "bond", "inflation"]
    if any(w in lower for w in fin_words):
        return "finance_claim"

    legal_words = ["illegal", "compliance", "statute", "regulation", "liable"]
    if any(w in lower for w in legal_words):
        return "legal_claim"

    return "general_claim"

def has_any_evidence(evidence_text: Optional[str]) -> Dict[str, Any]:
    if not evidence_text:
        return {"provided": False, "has_url": False, "has_doi": False, "has_pmid": False}
    e = evidence_text.strip()
    return {
        "provided": bool(e),
        "has_url": bool(URL_RE.search(e)),
        "has_doi": bool(DOI_RE.search(e)),
        "has_pmid": bool(PMID_RE.search(e)),
    }

def implausibility_caps(text: str) -> Dict[str, Any]:
    lower = text.lower()
    tags = []
    cap = None

    if "moon" in lower and ("1km" in lower or "1 km" in lower):
        tags.append("world_knowledge_red_flag")
        cap = 25

    if "made of candy" in lower:
        tags.append("absurdity_red_flag")
        cap = 20 if cap is None else min(cap, 20)

    return {"cap": cap, "tags": tags}

def verdict_from_score(score: int) -> str:
    if score <= 55:
        return "High risk / do not rely"
    if score <= 75:
        return "Unclear / needs verification"
    return "Likely reliable (still verify for high-stakes use)"

def build_audit_fingerprint(payload: str) -> Dict[str, str]:
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {
        "sha256": sha,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

def score_one_claim(claim_text: str, evidence_info: Dict[str, Any]) -> Dict[str, Any]:
    sig = compute_signals(claim_text)
    claim_type = infer_claim_type(claim_text, sig)

    score = 78
    score -= min(sig.get("absolute_count", 0) * 3, 12)

    needs_evidence = claim_type in (
        "numeric_or_stat_claim",
        "medical_claim",
        "legal_claim",
        "finance_claim",
    )

    evidence_present = evidence_info.get("provided") and (
        evidence_info.get("has_url")
        or evidence_info.get("has_doi")
        or evidence_info.get("has_pmid")
    )

    risk_tags: List[str] = []

    if needs_evidence:
        if not evidence_present:
            risk_tags.append("citation_unverified")
            if claim_type == "numeric_or_stat_claim":
                risk_tags.append("numeric_claim")
            score = min(score, 55)
        else:
            risk_tags.append("evidence_unvalidated")
            score = min(score, 75)

    imp = implausibility_caps(claim_text)
    if imp["cap"] is not None:
        score = min(score, int(imp["cap"]))
        risk_tags.extend(imp["tags"])

    if evidence_present and imp["cap"] is None:
        if needs_evidence:
            score = min(score + 3, 75)
        else:
            score = min(score + 6, 90)

    score = max(0, min(100, int(score)))
    verdict = verdict_from_score(score)

    evidence_needed = None
    if "citation_unverified" in risk_tags:
        evidence_needed = {
            "required": True,
            "reason": "High-liability claim without attached evidence.",
            "acceptable_evidence_examples": [
                "Peer-reviewed paper link (DOI/PMID/URL)",
                "Clinical guideline link",
                "Regulatory label URL",
            ],
            "suggested_query": f"{claim_text} PMID",
        }

    return {
        "text": claim_text,
        "claim_type": claim_type,
        "signals": sig,
        "risk_tags": list(dict.fromkeys(risk_tags)),
        "score": score,
        "verdict": verdict,
        "evidence_needed": evidence_needed,
    }

# ----------------------------
# API
# ----------------------------
@app.post("/verify")
def verify(req: VerifyRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text'")

    evidence_info = has_any_evidence(req.evidence)

    claims: List[Dict[str, Any]] = []
    if extract_claims:
        try:
            parsed = extract_claims(text)
            if isinstance(parsed, list) and parsed:
                for c in parsed:
                    if isinstance(c, dict) and "text" in c:
                        claims.append({"text": str(c.get("text", "")).strip()})
                    elif isinstance(c, str):
                        claims.append({"text": c.strip()})
        except Exception:
            claims = []

    if not claims:
        claims = [{"text": text}]

    scored_claims: List[Dict[str, Any]] = []
    claim_scores: List[int] = []

    for c in claims:
        claim_text = str(c.get("text", "")).strip() or text
        scored = score_one_claim(claim_text, evidence_info)
        scored_claims.append(scored)
        claim_scores.append(int(scored["score"]))

    overall_score = min(claim_scores) if claim_scores else 55
    overall_verdict = verdict_from_score(overall_score)

    payload_for_fingerprint = f"{text}|{req.evidence or ''}|{overall_score}|{overall_verdict}"
    audit_fp = build_audit_fingerprint(payload_for_fingerprint)

    return {
        "audit_fingerprint": audit_fp,
        "event_id": audit_fp["sha256"][:12],
        "input": {
            "length_chars": len(text),
            "num_claims": len(scored_claims),
        },
        "score": overall_score,
        "verdict": overall_verdict,
        "claims": scored_claims,
        "explanation": (
            "MVP heuristic verification. Evidence is detected but not validated. "
            "High-liability claims cannot reach 'Likely reliable' without true validation."
        ),
        "evidence": {
            "provided": bool(evidence_info.get("provided")),
            "signals": {
                "has_url": bool(evidence_info.get("has_url")),
                "has_doi": bool(evidence_info.get("has_doi")),
                "has_pmid": bool(evidence_info.get("has_pmid")),
            }
        }
    }
