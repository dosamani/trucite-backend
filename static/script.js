async function scoreText() {
  const inputEl = document.getElementById("inputText");
  const evidenceEl = document.getElementById("evidenceText");

  const text = (inputEl?.value || "").trim();
  const evidence = (evidenceEl?.value || "").trim();

  if (!text) {
    alert("Please paste AI/agent output text to verify.");
    return;
  }

  // UI: reset
  const scoreDisplay = document.getElementById("scoreDisplay");
  const scoreVerdict = document.getElementById("scoreVerdict");
  const resultEl = document.getElementById("result");
  const gaugeFill = document.getElementById("gaugeFill");

  if (scoreDisplay) scoreDisplay.textContent = "--";
  if (scoreVerdict) scoreVerdict.textContent = "Scoring…";
  if (resultEl) resultEl.textContent = "";

  // Gauge reset
  if (gaugeFill) {
    gaugeFill.style.strokeDasharray = "260";
    gaugeFill.style.strokeDashoffset = "260";
  }

  try {
    const res = await fetch("/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, evidence })
    });

    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      throw new Error("Backend returned a non-JSON response.");
    }

    // Display JSON
    if (resultEl) resultEl.textContent = JSON.stringify(data, null, 2);

    // Score + verdict
    const score = (data && typeof data.score === "number") ? data.score : null;
    const verdict = (data && data.verdict) ? data.verdict : "No verdict";

    if (scoreDisplay) scoreDisplay.textContent = score !== null ? String(score) : "--";
    if (scoreVerdict) scoreVerdict.textContent = verdict;

    // Gauge fill (0–100)
    if (gaugeFill && score !== null) {
      const dashTotal = 260;
      const clamped = Math.max(0, Math.min(100, score));
      const pct = clamped / 100;
      const offset = dashTotal - dashTotal * pct;
      gaugeFill.style.strokeDashoffset = String(offset);
    }

  } catch (err) {
    if (scoreVerdict) scoreVerdict.textContent = "Error scoring";
    if (resultEl) resultEl.textContent = String(err);
  }
}
