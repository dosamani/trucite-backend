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

// ✅ IMPORTANT: same-origin endpoint (NO CORS problems)
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

  // UI loading state
  scoreDisplay.textContent = "--";
  scoreVerdict.textContent = "Scoring…";
  result.textContent = "Contacting TruCite backend…";

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

    // If endpoint exists but wrong method, you'll see 405 here
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      throw new Error(`Backend error ${res.status}. ${txt}`);
    }

    const data = await res.json();
    const rawScore = (data.truth_score ?? data.score ?? 0);
    const score = Math.max(0, Math.min(100, Number(rawScore)));
    const verdict = String(data.verdict ?? verdictFromScore(score));

    scoreDisplay.textContent = `${score}`;
    scoreVerdict.textContent = verdict;

    // Gauge fill
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
      "If you see 404 here, your backend does NOT have /truth-score deployed.\n" +
      "If you see 405, endpoint exists but method mismatch.\n\n" +
      "Error: " + (e?.message || e);
  }
}

function verdictFromScore(score) {
  if (score >= 85) return "Likely True / Well-Supported";
  if (score >= 65) return "Plausible / Needs Verification";
  if (score >= 40) return "Questionable / High Uncertainty";
  return "Likely False / Misleading";
}
