async function scoreText() {
  const inputEl = document.getElementById("inputText");
  const resultEl = document.getElementById("result");
  const scoreDisplay = document.getElementById("scoreDisplay");
  const scoreVerdict = document.getElementById("scoreVerdict");
  const gaugeFill = document.getElementById("gaugeFill");

  const text = (inputEl?.value || "").trim();

  // UI reset
  resultEl.textContent = "";
  scoreDisplay.textContent = "--";
  scoreVerdict.textContent = "Scoring…";
  if (gaugeFill) gaugeFill.style.strokeDashoffset = "260";

  if (!text) {
    scoreVerdict.textContent = "Paste some AI output first.";
    resultEl.textContent = "No input provided.";
    return;
  }

  try {
    const res = await fetch("/score", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }) // IMPORTANT: backend expects { text: "..." }
    });

    // If backend throws an HTML error page, show it
    const raw = await res.text();

    if (!res.ok) {
      scoreVerdict.textContent = "Error communicating with TruCite engine.";
      resultEl.textContent =
        `HTTP ${res.status} ${res.statusText}\n\n` +
        raw.slice(0, 4000);
      return;
    }

    let data;
    try {
      data = JSON.parse(raw);
    } catch (e) {
      scoreVerdict.textContent = "Engine returned non-JSON response.";
      resultEl.textContent = raw.slice(0, 4000);
      return;
    }

    // Render score
    const score = Number(data.score ?? 0);
    const verdict = data.verdict || data.scoreVerdict || "—";

    scoreDisplay.textContent = isFinite(score) ? String(score) : "--";
    scoreVerdict.textContent = verdict;

    // Gauge fill (0–100)
    const dashTotal = 260;
    const clamped = Math.max(0, Math.min(100, score));
    const offset = dashTotal - (dashTotal * clamped) / 100;
    if (gaugeFill) gaugeFill.style.strokeDashoffset = String(offset);

    // Pretty print details
    resultEl.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    scoreVerdict.textContent = "Error communicating with TruCite engine.";
    resultEl.textContent =
      `Network/JS error:\n${String(err)}\n\n` +
      "If this persists, the backend may be restarting on Render.";
  }
}
