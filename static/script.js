
// ================================
// TruCite Frontend Script (FINAL)
// Same-origin backend via Render
// ================================

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

// 🔒 SAME-ORIGIN API ENDPOINT (NO CORS)
const BACKEND_ENDPOINT = "/api/score";

// Main verify function
async function scoreText() {
  const input = document.getElementById("inputText");
  const result = document.getElementById("result");
  const scoreDisplay = document.getElementById("scoreDisplay");
  const scoreVerdict = document.getElementById("scoreVerdict");
  const gaugeFill = document.getElementById("gaugeFill");

  const text = (input?.value || "").trim();
  if (!text) {
    result.textContent = "Paste some AI or agent output, then tap VERIFY.";
    return;
  }

  // UI: loading state
  scoreDisplay.textContent = "--";
  scoreVerdict.textContent = "Scoring…";
  result.textContent = "Contacting TruCite verification layer…";

  // Reset gauge
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

    if (!res.ok) {
      const msg = await res.text();
      throw new Error(`HTTP ${res.status}: ${msg}`);
    }

    const data = await res.json();

    const score = Math.max(0, Math.min(100, Number(data.score || 0)));
    const verdict = data.verdict || verdictFromScore(score);

    // Update score
    scoreDisplay.textContent = score;
    scoreVerdict.textContent = verdict;

    // Animate gauge
    const dashTotal = 260;
    const filled = (score / 100) * dashTotal;
    const offset = dashTotal - filled;

    if (gaugeFill) {
      setTimeout(() => {
        gaugeFill.style.transition = "stroke-dashoffset 1.1s ease";
        gaugeFill.style.strokeDashoffset = String(offset);
      }, 40);
    }

    // Output details
    result.textContent = JSON.stringify(data, null, 2);

  } catch (err) {
    // HARD FAIL — NO FAKE SCORE
    scoreDisplay.textContent = "--";
    scoreVerdict.textContent = "Backend connection failed";

    result.textContent =
      "❌ POST failed.\n\n" +
      "Endpoint: " + BACKEND_ENDPOINT + "\n\n" +
      "Error: " + (err?.message || err);
  }
}

// Score → verdict mapping
function verdictFromScore(score) {
  if (score >= 85) return "Likely True / Well-Supported";
  if (score >= 65) return "Plausible / Needs Verification";
  if (score >= 40) return "Questionable / High Uncertainty";
  return "Likely False / Misleading";
}
