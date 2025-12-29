async function scoreText() {
  const input = document.getElementById("inputText");
  const result = document.getElementById("result");
  const scoreDisplay = document.getElementById("scoreDisplay");
  const scoreVerdict = document.getElementById("scoreVerdict");
  const gaugeFill = document.getElementById("gaugeFill");

  if (!input || !result || !scoreDisplay || !scoreVerdict || !gaugeFill) {
    console.error("Missing required elements. Check IDs in index.html.");
    return;
  }

  const text = (input.value || "").trim();

  result.textContent = "Analyzing…";
  scoreDisplay.textContent = "--";
  scoreVerdict.textContent = "Score pending…";

  // reset gauge
  gaugeFill.style.strokeDashoffset = "260";

  try {
    const resp = await fetch("/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });

    if (!resp.ok) {
      throw new Error("HTTP " + resp.status);
    }

    const data = await resp.json();

    const score = Number(data.score || 0);
    const verdict = data.verdict || "--";

    scoreDisplay.textContent = String(score);
    scoreVerdict.textContent = verdict;

    // gauge fill (0..100 maps to 260..0 dashoffset)
    const clamped = Math.max(0, Math.min(100, score));
    const offset = 260 - (260 * (clamped / 100));
    gaugeFill.style.strokeDashoffset = String(offset);

    result.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    console.error(e);
    result.textContent = "Error communicating with TruCite engine.";
    scoreVerdict.textContent = "Error";
  }
}
