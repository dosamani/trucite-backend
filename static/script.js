// TruCite Frontend Script (Render-hosted, NO Neocities)

// ✅ same-origin endpoint (works because frontend + backend are on same Render domain)
const BACKEND_ENDPOINT = "/truth-score";

async function scoreText() {
  const input = document.getElementById("inputText");
  const result = document.getElementById("result");
  const scoreDisplay = document.getElementById("scoreDisplay");
  const scoreVerdict = document.getElementById("scoreVerdict");
  const gaugeFill = document.getElementById("gaugeFill");

  const text = (input?.value || "").trim();
  if (!text) {
    result.textContent = "Paste some AI output first, then tap VERIFY.";
    return;
  }

  scoreDisplay.textContent = "--";
  scoreVerdict.textContent = "Scoring…";
  result.textContent = "Contacting TruCite backend…";

  if (gaugeFill) {
    gaugeFill.style.transition = "none";
    gaugeFill.style.strokeDashoffset = "260";
  }

  try {
    const res = await fetch(BACKEND_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      throw new Error(`Backend error ${res.status}: ${JSON.stringify(data)}`);
    }

    const rawScore = (data.truth_score ?? data.score ?? 0);
    const score = Math.max(0, Math.min(100, Number(rawScore)));

    scoreDisplay.textContent = `${score}`;
    scoreVerdict.textContent = data.verdict || "—";

    const dashTotal = 260;
    const offset = dashTotal - (score / 100) * dashTotal;

    if (gaugeFill) {
      setTimeout(() => {
        gaugeFill.style.transition = "stroke-dashoffset 1.1s ease";
        gaugeFill.style.strokeDashoffset = String(offset);
      }, 40);
    }

    result.textContent = JSON.stringify(data, null, 2);

  } catch (e) {
    scoreDisplay.textContent = "--";
    scoreVerdict.textContent = "Backend connection failed";
    result.textContent =
      "❌ POST failed.\n\n" +
      "Endpoint: " + BACKEND_ENDPOINT + "\n\n" +
      "Error: " + (e?.message || e);
  }
}
