from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import re
import time
import hashlib
from typing import Optional, Dict, Any

app = FastAPI(title="TruCite Backend")

# Allow your frontend (Neocities / Render / local) to talk to this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------
# Models
# ------------------------------

class VerifyRequest(BaseModel):
    text: str
    evidence: Optional[str] = None

# ------------------------------
# Utility functions
# ------------------------------

def has_numeric(text: str) -> bool:
    return bool(re.search(r"\d", text))

def has_percent(text: str) -> bool:
    return "%" in text

def has_medical_terms(text: str) -> bool:
    keywords = [
        "aspirin", "mi", "myocardial", "infarction", "stroke",
        "cancer", "mortality", "risk", "treatment", "trial"
    ]
    return any(k in text.lower() for k in keywords)

def has_unreliable_claim(text: str) -> bool:
    """Detect obviously false claims like 'moon is 1km from earth'."""
    low_quality_patterns = [
        r"moon.*1\s*km",
        r"earth.*flat",
        r"vaccines.*microchips",
        r"5g.*causes"
    ]
    return any(re.search(p, text.lower()) for p in low_quality_patterns)

def has_evidence_signals(evidence: Optional[str]) -> bool:
    if not evidence:
        return False
    evidence = evidence.lower()
    return any([
        "pubmed" in evidence,
        "pmid" in evidence,
        "doi" in evidence,
        "nih.gov" in evidence,
        "ncbi.nlm.nih.gov" in evidence,
        "clinicaltrials.gov" in evidence
    ])

def compute_audit_fingerprint(text: str) -> Dict[str, Any]:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "sha256": sha,
        "timestamp_utc": ts
    }

# ------------------------------
# Routes
# ------------------------------

@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "TruCite Backend",
        "message": "Root endpoint is live. Use POST /verify to score text."
    }

@app.post("/verify")
def verify(req: VerifyRequest = Body(...)):
    text = req.text.strip()
    evidence = req.evidence

    # Base score
    score = 75  # neutral starting point
    verdict = "Unclear / needs verification"
    risk_tags = []

    # Fingerprint
    fingerprint = compute_audit_fingerprint(text)

    # ---- Heuristics ----

    # Obviously false claims → hard penalty
    if has_unreliable_claim(text):
        score = 20
        verdict = "High risk / do not rely"
        risk_tags.append("implausible_claim")

    # Numeric medical claims without evidence → penalty
    elif has_medical_terms(text) and has_numeric(text) and not has_evidence_signals(evidence):
        score = 55
        verdict = "High risk / do not rely"
        risk_tags.append("medical_numeric_no_evidence")

    # Numeric claim with evidence → boost
    elif has_medical_terms(text) and has_numeric(text) and has_evidence_signals(evidence):
        score = 80
        verdict = "Unclear / needs verification"
        risk_tags.append("evidence_provided")

    # Simple non-numeric general claim → mild uncertainty
    elif not has_numeric(text):
        score = 70
        verdict = "Unclear / needs verification"

    # Cap boundaries
    score = max(10, min(95, score))

    # ---- Response ----
    response = {
        "audit_fingerprint": fingerprint,
        "event_id": fingerprint["sha256"][:12],
        "input": {
            "length_chars": len(text),
            "num_claims": 1
        },
        "score": score,
        "verdict": verdict,
        "claims": [
            {
                "text": text,
                "score": score,
                "verdict": verdict,
                "risk_tags": risk_tags
            }
        ],
        "explanation": (
            "MVP heuristic verification. TruCite flags risk via numeric patterns, "
            "implausibility checks, citation signals, and medical domain sensitivity. "
            "Enterprise mode adds source validation and drift tracking."
        ),
        "evidence": {
            "provided": bool(evidence),
            "signals": {
                "has_url": bool(evidence and "http" in evidence),
                "has_pmid": bool(evidence and "pmid" in evidence.lower())
            }
        }
    }

    return response
