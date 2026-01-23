// static/script.js — TruCite demo client (Decision Gate enabled)

function clamp(n, min, max) { return Math.max(min, Math.min(max, n)); }

function setGauge(score) {
  const s = clamp(Number(score || 0), 0, 100);
  const dash = 260;
  const offset = dash - (dash * (s / 100));
  const fill = document.getElementById("gaugeFill");
  if (fill) fill.style.strokeDashoffset = String(offset);
}

function ensurePolicyUI() {
  // Create a small policy selector above the VERIFY button if not present
  if (document.getElementById("policyMode")) return;

  const verifySection = document.querySelector(".verify-section");
  if (!verifySection) return;

  // Try to find textarea to insert around it
  const textarea = document.getElementById("inputText");
  if (!textarea) return;

  const wrap = document.createElement("div");
  wrap.style.maxWidth = "720px";
  wrap.style.margin = "12px auto 0";
  wrap.style.textAlign = "left";

  const label = document.createElement("div");
  label.textContent = "Policy Mode (Decision Gate)";
  label.style.fontWeight = "900";
  label.style.color = "#FFD700";
  label.style.marginBottom = "6px";

  const select = document.createElement("select");
  select.id = "policyMode";
  select.style.width = "100%";
  select.style.background = "#000";
  select.style.color = "#fff";
  select.style.border = "1px solid rgba(255,215,0,0.55)";
  select.style.borderRadius = "12px";
  select.style.padding = "10px 12px";
  select.style.fontWeight = "800";
  select.style.boxShadow = "0 0 18px rgba(255,215,0,0.06)";

  const opts = [
    { v: "consumer", t: "Consumer (softer)" },
    { v: "enterprise", t: "Enterprise (default)" },
    { v: "regulated", t: "Regulated (strict)" }
  ];
  opts.forEach(o => {
    const op = document.createElement("option");
    op.value = o.v;
    op.textContent = o.t;
    if (o.v === "enterprise") op.selected = true;
    select.appendChild(op);
  });

  wrap.appendChild(label);
  wrap.appendChild(select);

  // Insert right after textarea
  textarea.insertAdjacentElement("afterend", wrap);
}

function setDecisionUI(decision) {
  const verdictEl = document.getElementById("scoreVerdict");
  if (!verdictEl) return;

  if (!decision || !decision.action) return;

  const action = String(decision.action).toUpperCase();
  const reason = decision.reason ? String(decision.reason) : "";

  // Put decision on top line, keep existing verdict as second line (handled in scoreText)
  verdictEl.innerHTML = `<strong>${action}</strong> — ${reason}`;
}

async function scoreText() {
  ensurePolicyUI();

  const input = document.getElementById("inputText");
  const result = document.getElementById("result");
  const scoreDisplay = document.getElementById("scoreDisplay");
  const scoreVerdict = document.getElementById("scoreVerdict");

  const policyModeEl = document.getElementById("policyMode");
  const policy_mode = policyModeEl ? policyModeEl.value : "enterprise";

  // Evidence box may or may not exist in your current HTML.
  // If you add <textarea id="evidenceText"> it will be sent.
  const evidenceEl = document.getElementById("evidenceText");
  const evidence = evidenceEl ? evidenceEl.value : "";

  if (!input || !result || !scoreDisplay || !scoreVerdict) return;

  const text = (input.value || "").trim();
  if (!text) {
    scoreDisplay.textContent = "--";
    scoreVerdict.textContent = "Paste text to verify.";
    result.textContent = "";
    setGauge(0);
    return;
  }

  scoreDisplay.textContent = "--";
  scoreVerdict.textContent = "Score pending…";
  result.textContent = "";

  try {
    const resp = await fetch("/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, evidence, policy_mode })
    });

    if (!resp.ok) {
      const errText = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${errText}`);
    }

    const data = await resp.json();

    const score = (typeof data.score === "number") ? data.score : 0;
    scoreDisplay.textContent = String(score);
    setGauge(score);

    // Show decision gate result prominently
    if (data.decision) {
      setDecisionUI(data.decision);
    } else {
      scoreVerdict.textContent = data.verdict || "Result returned.";
    }

    // Always show JSON details
    result.textContent = JSON.stringify(data, null, 2);

  } catch (e) {
    scoreDisplay.textContent = "--";
    scoreVerdict.textContent = "Error communicating with TruCite engine.";
    result.textContent = String(e && e.message ? e.message : e);
    setGauge(0);
  }
}

// Make sure policy UI appears even before first click (best effort)
document.addEventListener("DOMContentLoaded", () => {
  try { ensurePolicyUI(); } catch (_) {}
});
