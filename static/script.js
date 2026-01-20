// /static/script.js
const API_BASE = "https://trucite-backend.onrender.com";

window.addEventListener("load", () => {
  // quick visible sanity check in case script isn't loading
  console.log("TruCite script.js loaded");
});

async function scoreText() {
  const input = document.getElementById("inputText");
  const result = document.getElementById("result");
  const scoreDisplay = document.getElementById("scoreDisplay");
  const scoreVerdict = document.getElementById("scoreVerdict");
  const gaugeFill = document.getElementById("gaugeFill");

  if (!input || !result || !scoreDisplay || !scoreVerdict || !gaugeFill) {
    const missing = [
      !input ? "inputText" : null,
      !result ? "result" : null,
      !scoreDisplay ? "scoreDisplay" : null,
      !scoreVerdict ? "scoreVerdict" : null,
      !gaugeFill ? "gaugeFill" : null
    ].filter(Boolean).join(", ");
    alert("Missing HTML element IDs: " + missing);
    return;
  }

  const text = (input.value || "").trim();
  if (!text) {
    result.textContent = "Paste AI output above, then tap VERIFY.";
    scoreDisplay.textContent = "--";
    scoreVerdict.textContent = "Score pending…";
    gaugeFill.style.strokeDashoffset = "260";
    return;
  }

  result.textContent = "Analyzing…";
  scoreDisplay.textContent = "--";
  scoreVerdict.textContent = "Calling engine…";
  gaugeFill.style.strokeDashoffset = "260";

  try {
    const resp = await fetch(`${API_BASE}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });

    const bodyText = await resp.text(); // read once
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}: ${bodyText}`);
    }

    let data;
    try {
      data = JSON.parse(bodyText);
    } catch {
      throw new Error("Engine returned non-JSON: " + bodyText.slice(0, 200));
    }

    const score = Number(data?.score ?? data?.claims?.[0]?.score ?? 0);
    const verdict = data?.verdict ?? data?.claims?.[0]?.verdict ?? "—";

    scoreDisplay.textContent = String(score);
    scoreVerdict.textContent = verdict;

    const clamped = Math.max(0, Math.min(100, score));
    const offset = 260 - (260 * (clamped / 100));
    gaugeFill.style.strokeDashoffset = String(offset);

    result.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    scoreVerdict.textContent = "Error";
    scoreDisplay.textContent = "--";
    gaugeFill.style.strokeDashoffset = "260";
    result.textContent = `Error communicating with TruCite engine:\n${e.message}`;
    alert("VERIFY failed: " + e.message);
  }
}
