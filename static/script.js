// ===============================
// TruCite Frontend Script (FULL)
// ===============================

// Accordion behavior (FAQ / Founder / Legal)
document.addEventListener("DOMContentLoaded", () => {
  const buttons = document.querySelectorAll(".accordion-btn");
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      btn.classList.toggle("active");
      const panel = btn.nextElementSibling;
      if (!panel) return;
      const isOpen = panel.style.display === "block";
      panel.style.display = isOpen ? "none" : "block";
    });
  });
});

// ✅ Permanent: same-origin endpoint when hosted on Render
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

    const rawText = await res.text();
    if (!res.ok) throw new Error(`Backend ${res.status}: ${rawText}`);

    const data = JSON.parse(rawText);

    const rawScore = (data.score ?? data.truth_score ?? 0);
    const score = Math.max(0, Math.min(100, Number(rawScore)));
    const verdict = String(data.verdict ?? verdictFromScore(score));

    scoreDisplay.textContent = `${score}`;
    scoreVerdict.textContent = verdict;

    const dashTotal = 260;
    const filled = (score / 100) * dashTotal;
    const offset = dashTotal - filled;

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

function verdictFromScore(score) {
  if (score >= 85) return "Likely True / Well-Supported";
  if (score >= 65) return "Plausible / Needs Verification";
  if (score >= 40) return "Questionable / High Uncertainty";
  return "Likely False / Misleading";
}
