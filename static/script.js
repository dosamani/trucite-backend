// TruCite Frontend Script (v25)
// - Calls backend /verify endpoint
// - Updates gauge + score + verdict
// - Renders Decision Gate from backend: data.decision.action + data.decision.reason
// - Adds copy buttons: payload / curl / response

const API_PATH = "/verify";

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

function setGauge(score) {
  const gaugeFill = document.getElementById("gaugeFill");
  const scoreDisplay = document.getElementById("scoreDisplay");

  const dashTotal = 260;
  const s = clamp(Number(score) || 0, 0, 100);

  const offset = dashTotal - (dashTotal * s) / 100;
  gaugeFill.style.strokeDashoffset = String(offset);

  scoreDisplay.textContent = String(Math.round(s));
}

function setVerdictText(score, verdictTextFromAPI) {
  const scoreVerdict = document.getElementById("scoreVerdict");
  const s = clamp(Number(score) || 0, 0, 100);

  if (verdictTextFromAPI && typeof verdictTextFromAPI === "string") {
    scoreVerdict.textContent = verdictTextFromAPI;
    return;
  }

  if (s >= 85) scoreVerdict.textContent = "High reliability (demo)";
  else if (s >= 60) scoreVerdict.textContent = "Moderate reliability (review recommended)";
  else scoreVerdict.textContent = "Low reliability (high risk)";
}

function setDecisionGateFromBackend(data, score) {
  const gateActionEl = document.getElementById("gateAction");
  const gateReasonEl = document.getElementById("gateReason");

  const decisionObj = data.decision || data.decision_gate || data.gate || null;

  let action = null;
  let reason = null;

  if (decisionObj && typeof decisionObj === "object") {
    action = decisionObj.action || decisionObj.outcome || decisionObj.label || null;
    reason = decisionObj.reason || decisionObj.rationale || decisionObj.explanation || null;
  }

  if (typeof action === "string") {
    const a = action.toUpperCase();
    if (a.includes("ALLOW") || a.includes("PASS") || a.includes("APPROVE")) action = "ALLOW";
    else if (a.includes("BLOCK") || a.includes("FAIL") || a.includes("DENY")) action = "BLOCK";
    else action = "REVIEW";
  }

  if (!action) {
    const s = clamp(Number(score) || 0, 0, 100);
    action = s >= 80 ? "ALLOW" : s < 50 ? "BLOCK" : "REVIEW";
    reason = "Inferred from Truth Score threshold (configurable).";
  }

  if (!reason) reason = "See validation details below.";

  gateActionEl.textContent = action;
  gateReasonEl.textContent = reason;

  gateActionEl.style.color =
    action === "ALLOW" ? "#32D583" : action === "BLOCK" ? "#F04438" : "#F79009";
}

let lastResponseJson = null;

function buildPayload() {
  const inputEl = document.getElementById("inputText");
  const evidenceEl = document.getElementById("evidenceText");

  const text = (inputEl.value || "").trim();
  const evidence = (evidenceEl.value || "").trim();

  return {
    text: text || "PASTE_TEXT_HERE",
    evidence: evidence || "",
    policy_mode: "enterprise"
  };
}

function showCopyStatus(msg) {
  const el = document.getElementById("copyStatus");
  if (!el) return;
  el.textContent = msg;
  setTimeout(() => { el.textContent = ""; }, 2500);
}

async function copyToClipboard(str) {
  try {
    await navigator.clipboard.writeText(str);
    return true;
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = str;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    return true;
  }
}

async function copyPayload() {
  const payload = buildPayload();
  await copyToClipboard(JSON.stringify(payload, null, 2));
  showCopyStatus("Copied JSON payload.");
}

async function copyCurl() {
  const payload = buildPayload();
  const base = window.location.origin;
  const endpoint = base + API_PATH;

  const curl =
    `curl -X POST "${endpoint}" \\\n` +
    `  -H "Content-Type: application/json" \\\n` +
    `  -d '${JSON.stringify(payload).replace(/'/g, "\\'")}'`;

  await copyToClipboard(curl);
  showCopyStatus("Copied curl command.");
}

async function copyResponse() {
  if (!lastResponseJson) {
    showCopyStatus("No response yet — tap VERIFY first.");
    return;
  }
  await copyToClipboard(JSON.stringify(lastResponseJson, null, 2));
  showCopyStatus("Copied response JSON.");
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
      body: JSON.stringify({ text, evidence, policy_mode: "enterprise" })
    });

    if (!resp.ok) {
      const errText = await resp.text();
      throw new Error(`Backend error (${resp.status}): ${errText}`);
    }

    const data = await resp.json();
    lastResponseJson = data;

    const score =
      data.score ??
      data.truth_score ??
      data.reliability_score ??
      data.truthScore ??
      data.result?.score ??
      data.result?.truth_score ??
      0;

    const verdict =
      data.verdict ??
      data.label ??
      data.result?.verdict ??
      data.result?.label ??
      null;

    setGauge(score);
    document.getElementById("scoreDisplay").textContent = String(Math.round(Number(score) || 0));
    setVerdictText(score, verdict);

    setDecisionGateFromBackend(data, score);

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
    resultEl.textContent = String(err?.message || err);
  }
}
