const VERIFY_ENDPOINT = "/verify";

let lastPayload = null;
let lastResponseText = "";
let lastCurl = "";

function setStatus(msg) {
  const el = document.getElementById("verifyStatus");
  if (el) el.textContent = msg;
}

function setDecision(action, reason) {
  const actionEl = document.getElementById("decisionAction");
  const reasonEl = document.getElementById("decisionReason");

  if (!actionEl || !reasonEl) return;

  actionEl.classList.remove("action-ALLOW", "action-REVIEW", "action-BLOCK");
  actionEl.textContent = action || "—";
  reasonEl.textContent = reason || "";

  if (action === "ALLOW") actionEl.classList.add("action-ALLOW");
  if (action === "REVIEW") actionEl.classList.add("action-REVIEW");
  if (action === "BLOCK") actionEl.classList.add("action-BLOCK");
}

function setGauge(score) {
  const scoreDisplay = document.getElementById("scoreDisplay");
  const scoreVerdict = document.getElementById("scoreVerdict");
  const gaugeFill = document.getElementById("gaugeFill");

  if (scoreDisplay) scoreDisplay.textContent = (score === null || score === undefined) ? "--" : String(score);
  if (scoreVerdict) scoreVerdict.textContent = (score === null || score === undefined) ? "Score pending…" : "";

  if (!gaugeFill) return;

  const dash = 260; // path dasharray
  if (score === null || score === undefined) {
    gaugeFill.style.strokeDashoffset = String(dash);
    return;
  }
  const clamped = Math.max(0, Math.min(100, score));
  const offset = dash - (dash * (clamped / 100));
  gaugeFill.style.strokeDashoffset = String(offset);
}

function pretty(obj) {
  try { return JSON.stringify(obj, null, 2); } catch { return String(obj); }
}

async function scoreText() {
  const inputEl = document.getElementById("inputText");
  const evidenceEl = document.getElementById("evidenceText");
  const resultEl = document.getElementById("result");

  const text = (inputEl?.value || "").trim();
  const evidence = (evidenceEl?.value || "").trim();

  // ✅ IMPORTANT: do nothing if no claim/text
  if (!text) {
    setStatus("Paste AI output first, then tap VERIFY.");
    setGauge(null);
    setDecision("REVIEW", "No input provided.");
    if (resultEl) resultEl.textContent = "No input provided. Paste text and try again.";
    return;
  }

  setStatus("Verifying…");
  setGauge(null);
  setDecision("—", "Awaiting verification…");
  if (resultEl) resultEl.textContent = "";

  const payload = {
    text,
    evidence: evidence || "",
    policy_mode: "enterprise"
  };

  lastPayload = payload;
  lastCurl =
    `curl -s -X POST "${location.origin}${VERIFY_ENDPOINT}" ` +
    `-H "Content-Type: application/json" ` +
    `-d '${JSON.stringify(payload)}'`;

  try {
    const res = await fetch(VERIFY_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      const errText = await res.text();
      lastResponseText = errText;
      setStatus(`Error: could not score. Backend returned ${res.status}.`);
      setDecision("REVIEW", `Backend error ${res.status}.`);
      if (resultEl) resultEl.textContent = `Backend error (${res.status}): ${errText}`;
      return;
    }

    const data = await res.json();
    lastResponseText = pretty(data);

    // score + verdict
    const score = (typeof data.score === "number") ? data.score : null;
    setGauge(score);

    const verdict = data.verdict || "—";
    const scoreVerdict = document.getElementById("scoreVerdict");
    if (scoreVerdict) scoreVerdict.textContent = verdict;

    // decision gate
    const action = data?.decision?.action || "—";
    const reason = data?.decision?.reason || "—";
    setDecision(action, reason);

    // render raw JSON response
    if (resultEl) resultEl.textContent = pretty(data);

    setStatus("Done. Tip: Add evidence (URL/DOI/PMID) for high-liability claims.");

  } catch (e) {
    lastResponseText = String(e);
    setStatus("Error: could not score. Check backend route and try again.");
    setGauge(null);
    setDecision("REVIEW", "Backend unavailable or route mismatch.");
    if (resultEl) resultEl.textContent = `Error: ${String(e)}`;
  }
}

/* =======================
   COPY HELPERS
======================= */
async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    setStatus("Copied to clipboard.");
  } catch {
    // fallback
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    setStatus("Copied to clipboard.");
  }
}

function copyJSONPayload() {
  if (!lastPayload) {
    setStatus("Nothing to copy yet. Run VERIFY first.");
    return;
  }
  copyToClipboard(pretty(lastPayload));
}

function copyCurl() {
  if (!lastCurl) {
    setStatus("Nothing to copy yet. Run VERIFY first.");
    return;
  }
  copyToClipboard(lastCurl);
}

function copyResponse() {
  if (!lastResponseText) {
    setStatus("Nothing to copy yet. Run VERIFY first.");
    return;
  }
  copyToClipboard(lastResponseText);
}
