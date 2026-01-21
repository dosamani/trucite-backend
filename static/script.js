// TruCite frontend script.js (v24)
// Calls backend POST /verify with { text, evidence }
// Updates gauge + result JSON

const API_VERIFY = "/verify";

function clamp(n, min, max) { return Math.max(min, Math.min(max, n)); }

function setGauge(score) {
  const fill = document.getElementById("gaugeFill");
  const scoreDisplay = document.getElementById("scoreDisplay");

  // SVG arc dash math
  const total = 260;
  const pct = clamp(score, 0, 100) / 100;
  const offset = total - (total * pct);

  if (fill) fill.style.strokeDashoffset = String(offset);
  if (scoreDisplay) scoreDisplay.textContent = String(score);
}

function setVerdict(text) {
  const el = document.getElementById("scoreVerdict");
  if (el) el.textContent = text || "Score pending…";
}

function setVerifyStatus(text) {
  const el = document.getElementById("verifyStatus");
  if (el) el.textContent = text || "";
}

function pretty(obj) {
  try { return JSON.stringify(obj, null, 2); }
  catch (e) { return String(obj); }
}

async function scoreText() {
  const inputEl = document.getElementById("inputText");
  const evidenceEl = document.getElementById("evidenceText");
  const resultEl = document.getElementById("result");

  const text = (inputEl?.value || "").trim();
  const evidence = (evidenceEl?.value || "").trim();

  if (!text) {
    setVerdict("Paste AI output to verify.");
    if (resultEl) resultEl.textContent = "";
    setVerifyStatus("Nothing submitted. Paste AI output above, then tap VERIFY.");
    return;
  }

  setGauge(0);
  setVerdict("Score pending…");
  setVerifyStatus(evidence ? "Evidence detected. Submitting claim + evidence…" : "No evidence provided. Submitting claim…");
  if (resultEl) resultEl.textContent = "Submitting…";

  try {
    const resp = await fetch(API_VERIFY, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, evidence: evidence || null })
    });

    if (!resp.ok) {
      const errText = await resp.text();
      setVerdict("Engine error");
      setVerifyStatus(`Error communicating with TruCite engine: HTTP ${resp.status}`);
      if (resultEl) resultEl.textContent = errText || `HTTP ${resp.status}`;
      return;
    }

    const data = await resp.json();

    // top-level score/verdict
    const score = typeof data.score === "number" ? data.score : 0;
    const verdict = data.verdict || "Unclear / needs verification";

    setGauge(score);
    setVerdict(verdict);

    // Evidence status line (frontend summary)
    const evidenceProvided = !!(data?.evidence?.provided);
    const hasUrl = !!(data?.evidence?.signals?.has_url);
    const hasPmid = !!(data?.evidence?.signals?.has_pmid);
    const hasDoi = !!(data?.evidence?.signals?.has_doi);

    if (evidenceProvided) {
      const parts = [];
      if (hasUrl) parts.push("URL");
      if (hasPmid) parts.push("PMID");
      if (hasDoi) parts.push("DOI");
      const types = parts.length ? parts.join(" + ") : "evidence text";
      setVerifyStatus(`Evidence detected (${types}). Note: MVP detects evidence presence; enterprise mode validates relevance.`);
    } else {
      setVerifyStatus("No evidence detected. High-liability numeric claims may be capped.");
    }

    if (resultEl) resultEl.textContent = pretty(data);

  } catch (err) {
    setVerdict("Engine error");
    setVerifyStatus("Error communicating with TruCite engine.");
    if (resultEl) resultEl.textContent = String(err);
  }
}
