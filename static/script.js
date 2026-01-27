// TruCite Frontend Script (v24-compatible)
// - Calls backend scoring endpoint
// - Updates gauge + score + verdict
// - Renders raw JSON into #result
// - Adds Decision Gate block (ALLOW / REVIEW / BLOCK)

const API_PATH = "/score"; // <-- change to "/verify" if your backend route differs

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

function setGauge(score) {
  const gaugeFill = document.getElementById("gaugeFill");
  const scoreDisplay = document.getElementById("scoreDisplay");

  const dashTotal = 260; // matches your stroke-dasharray
  const s = clamp(Number(score) || 0, 0, 100);

  const offset = dashTotal - (dashTotal * s) / 100;
  gaugeFill.style.strokeDashoffset = String(offset);

  scoreDisplay.textContent = String(Math.round(s));
}

function setVerdictText(score, verdictTextFromAPI) {
  const scoreVerdict = document.getElementById("scoreVerdict");
  const s = clamp(Number(score) || 0, 0, 100);

  // If backend gave explicit verdict, prefer it
  if (verdictTextFromAPI && typeof verdictTextFromAPI === "string") {
    scoreVerdict.textContent = verdictTextFromAPI;
    return;
  }

  // Otherwise infer something reasonable
  if (s >= 85) scoreVerdict.textContent = "High reliability (demo)";
  else if (s >= 60) scoreVerdict.textContent = "Moderate reliability (review recommended)";
  else scoreVerdict.textContent = "Low reliability (high risk)";
}

function setDecisionGate(data, score) {
  const gateActionEl = document.getElementById("gateAction");
  const gateReasonEl = document.getElementById("gateReason");

  // Try multiple schema shapes
  const gate =
    data.decision_gate ||
    data.decision ||
    data.gate ||
    data.verdict_action ||
    data.action ||
    null;

  let action = null;

  // Normalize action
  if (typeof gate === "string") {
    const g = gate.toUpperCase();
    if (g.includes("ALLOW") || g.includes("PASS") || g.includes("APPROVE")) action = "ALLOW";
    else if (g.includes("BLOCK") || g.includes("FAIL") || g.includes("DENY")) action = "BLOCK";
    else action = "REVIEW";
  } else if (gate && typeof gate === "object") {
    const raw = (gate.action || gate.outcome || gate.label || "").toString().toUpperCase();
    if (raw.includes("ALLOW") || raw.includes("PASS") || raw.includes("APPROVE")) action = "ALLOW";
    else if (raw.includes("BLOCK") || raw.includes("FAIL") || raw.includes("DENY")) action = "BLOCK";
    else action = "REVIEW";
  }

  // Reason
  const reason =
    (gate && typeof gate === "object" && (gate.reason || gate.rationale || gate.explanation)) ||
    data.gate_reason ||
    data.reason ||
    data.rationale ||
    data.explanation ||
    "See validation details below.";

  // If no gate returned, infer from score (configurable thresholds)
  if (!action) {
    const s = clamp(Number(score) || 0, 0, 100);
    action = s >= 80 ? "ALLOW" : s < 50 ? "BLOCK" : "REVIEW";
    gateReasonEl.textContent = "Inferred from Truth Score threshold (configurable).";
  } else {
    gateReasonEl.textContent = reason;
  }

  gateActionEl.textContent = action;

  // Simple color cue (no CSS dependency)
  gateActionEl.style.color =
    action === "ALLOW" ? "#32D583" : action === "BLOCK" ? "#F04438" : "#F79009";
}

async function scoreText() {
  const inputEl = document.getElementById("inputText");
  const evidenceEl = document.getElementById("evidenceText");
  const resultEl = document.getElementById("result");
  const verifyStatus = document.getElementById("verifyStatus");

  const text = (inputEl.value || "").trim();
  const evidence = (evidenceEl.value || "").trim();

  if (!text) {
    verifyStatus.textContent = "Please paste some AI output to verify.";
    return;
  }

  verifyStatus.textContent = "Verifying…";

  // Reset UI quickly
  setGauge(0);
  document.getElementById("scoreDisplay").textContent = "--";
  document.getElementById("scoreVerdict").textContent = "Scoring…";
  document.getElementById("gateAction").textContent = "—";
  document.getElementById("gateReason").textContent = "Awaiting verification…";
  resultEl.textContent = "";

  try {
    const resp = await fetch(API_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        evidence
      })
    });

    if (!resp.ok) {
      const errText = await resp.text();
      throw new Error(`Backend error (${resp.status}): ${errText}`);
    }

    const data = await resp.json();

    // Try common score fields
    const score =
      data.truth_score ??
      data.score ??
      data.reliability_score ??
      data.truthScore ??
      data.result?.truth_score ??
      0;

    // Try common verdict fields
    const verdict =
      data.verdict ??
      data.label ??
      data.result?.verdict ??
      data.result?.label ??
      null;

    setGauge(score);
    document.getElementById("scoreDisplay").textContent = String(Math.round(Number(score) || 0));
    setVerdictText(score, verdict);

    // Decision gate (new)
    setDecisionGate(data, score);

    // Pretty print JSON result
    resultEl.textContent = JSON.stringify(data, null, 2);

    verifyStatus.textContent = evidence
      ? "Evidence provided — TruCite will evaluate relevance and risk posture."
      : "Tip: Provide evidence (URL/DOI/PMID) to reduce caps on high-liability numeric claims.";

  } catch (err) {
    console.error(err);
    verifyStatus.textContent = "Error: could not score. Check backend route and try again.";
    document.getElementById("scoreVerdict").textContent = "Error";
    document.getElementById("gateAction").textContent = "REVIEW";
    document.getElementById("gateReason").textContent = "Backend unavailable or route mismatch.";
    document.getElementById("result").textContent = String(err?.message || err);
  }
}
