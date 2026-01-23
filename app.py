# app.py — TruCite backend (Decision-Gated MVP)
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

# ---------------------------------------
# Optional claim parser (safe import)
# ---------------------------------------
extract_claims = None
try:
    from claim_parser import extract_claims as _extract_claims  # type: ignore
    extract_claims = _extract_claims
except Exception:
    extract_claims = None


app = FastAPI(title="TruCite Engine", version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------
# Static hosting
# ---------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
def home():
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h3>TruCite backend running. Missing index.html.</h3>")
    return HTMLResponse(index.read_text(encoding="utf-8"))


# ---------------------------------------
# Models
# ---------------------------------------
class VerifyRequest(BaseModel):
    text: str
    evidence: Optional[str] = None
    policy_mode: Optional[str] = "enterprise"


# ---------------------------------------
# Regex + constants
# ---------------------------------------
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
PMID_RE = re.compile(r"\bPMID[:\s]*\d{6,9}\b|\b\d{6,9}\b", re.IGNORECASE)

ABSOLUTE_WORDS = [
    "always", "never", "guaranteed", "proven", "certain", "definitely",
    "cannot", "must", "everyone", "nobody"
]


# ---------------------------------------
# Helpers
# ---------------------------------------
def compute_signals(text: str) -> Dict[str, Any]:
    lower = text.lower()
    numerics = re.findall(r"\d+(\.\d+)?", text)

    return {
        "has_url": bool(URL_RE.search(text)),
        "has_doi": bool(DOI_RE.search(text)),
        "has_pmid": bool(PMID_RE.search(text)),
        "has_year": bool(re.search(r"\b(19|20)\d{2}\b", text)),
        "has_percent": "%" in text,
        "has_numerics": bool(numerics),
        "numeric_count": len(numerics),
        "absolute_count": sum(lower.count(w) for w in ABSOLUTE_WORDS),
        "has_citation_like": bool(re.search(r"\(\d{4}\)|\[\d+\]", text)),
    }


def infer_claim_type(text: str, sig: Dict[str, Any]) -> str:
    lower = text.lower()
    if sig["has_percent"] or sig["numeric_count"] >= 2:
        return "numeric_or_stat_claim"
    if any(w in lower for w in ["aspirin", "mi", "myocardial", "stroke", "risk"]):
        return "medical_claim"
    if any(w in lower for w in ["roi", "interest", "yield"]):
        return "finance_claim"
    if any(w in lower for w in ["law", "illegal", "liable"]):
        return "legal_claim"
    return "general_claim"


def detect_evidence(text: Optional[str]) -> Dict[str, Any]:
    if not text:
        return {"provided": False}

    return {
        "provided": True,
        "has_url": bool(URL_RE.search(text)),
        "has_doi": bool(DOI_RE.search(text)),
        "has_pmid": bool(PMID_RE.search(text)),
        "pmids": re.findall(PMID_RE, text),
    }


def implausibility_check(text: str) -> Dict[str, Any]:
    lower = text.lower()
    tags = []

    if "moon" in lower and ("1km" in lower or "1 km" in lower):
        tags.append("world_knowledge_red_flag")

    if "made of candy" in lower:
        tags.append("absurdity_red_flag")

    return {
        "hard_block": bool(tags),
        "tags": tags,
    }


def verdict_from_score(score: int) -> str:
    if score < 55:
        return "High risk / do not rely"
    if score < 80:
        return "Unclear / needs verification"
    return "Likely reliable (still verify for high-stakes use)"


# ---------------------------------------
# Core scoring
# ---------------------------------------
def score_claim(text: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    sig = compute_signals(text)
    claim_type = infer_claim_type(text, sig)

    score = 80
    score -= min(sig["absolute_count"] * 3, 15)

    risk_tags = []

    high_liability = claim_type in {
        "numeric_or_stat_claim", "medical_claim", "legal_claim", "finance_claim"
    }

    evidence_valid = evidence.get("provided") and (
        evidence.get("has_pmid") or evidence.get("has_doi") or evidence.get("has_url")
    )

    if high_liability and not evidence_valid:
        score = min(score, 55)
        risk_tags.append("evidence_unvalidated")

    impl = implausibility_check(text)
    if impl["hard_block"]:
        score = min(score, 20)
        risk_tags.extend(impl["tags"])

    score = max(0, min(100, int(score)))

    return {
        "text": text,
        "claim_type": claim_type,
        "signals": sig,
        "risk_tags": list(set(risk_tags)),
        "score": score,
        "verdict": verdict_from_score(score),
    }


# ---------------------------------------
# Decision Gate (STEP 5)
# ---------------------------------------
def decision_gate(overall_score: int, claims: List[Dict[str, Any]], policy_mode: str):
    pm = policy_mode.lower()

    # WORLD KNOWLEDGE BLOCK
    for c in claims:
        if "world_knowledge_red_flag" in c["risk_tags"] or "absurdity_red_flag" in c["risk_tags"]:
            return {
                "action": "BLOCK",
                "policy_mode": pm,
                "reason": "World-knowledge red flag (hard block)."
            }

    # HIGH LIABILITY WITHOUT VALIDATION
    if any("evidence_unvalidated" in c["risk_tags"] for c in claims):
        if pm == "regulated":
            return {
                "action": "BLOCK",
                "policy_mode": pm,
                "reason": "High-liability claim without validated evidence."
            }
        return {
            "action": "REVIEW",
            "policy_mode": pm,
            "reason": "High-liability claim requires validated evidence; human review required."
        }

    if overall_score >= 80:
        return {"action": "ALLOW", "policy_mode": pm, "reason": "Meets trust threshold."}

    if overall_score < 55:
        return {"action": "BLOCK", "policy_mode": pm, "reason": "Low trust score."}

    return {"action": "REVIEW", "policy_mode": pm, "reason": "Intermediate confidence."}


# ---------------------------------------
# API
# ---------------------------------------
@app.post("/verify")
def verify(req: VerifyRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Missing text")

    text = req.text.strip()
    policy_mode = req.policy_mode or "enterprise"

    evidence_info = detect_evidence(req.evidence)

    # Promote PMID/DOI from claim text if no evidence box provided
    if not evidence_info.get("provided"):
        promoted = detect_evidence(text)
        if promoted.get("has_pmid") or promoted.get("has_doi"):
            evidence_info = promoted

    claims = [{"text": text}]
    scored_claims = [score_claim(text, evidence_info)]
    scores = [c["score"] for c in scored_claims]
    overall_score = min(scores)

    decision = decision_gate(overall_score, scored_claims, policy_mode)

    fingerprint = hashlib.sha256(
        f"{text}|{overall_score}|{policy_mode}".encode()
    ).hexdigest()

    return {
        "audit_fingerprint": {
            "sha256": fingerprint,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        },
        "event_id": fingerprint[:12],
        "input": {
            "length_chars": len(text),
            "num_claims": 1,
            "policy_mode": policy_mode,
        },
        "score": overall_score,
        "verdict": verdict_from_score(overall_score),
        "decision": decision,
        "claims": scored_claims,
        "explanation": (
            "MVP heuristic verification + Decision Gate. "
            "Implements enforceable ALLOW / REVIEW / BLOCK logic."
        ),
        "evidence": evidence_info,
    }
