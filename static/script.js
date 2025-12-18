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

/**
 * ✅ Permanent fix:
 * If frontend + backend are hosted on the SAME Render service,
 * we call the backend using a RELATIVE URL (no domain).
 *
 * This avoids CORS completely.
 */
const BACKEND_ENDPOINT = "/truth-score";

// Demo scoring call
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

  // UI: loading state
  scoreDisplay.textContent = "--";
  scoreVerdict.textContent = "Scoring…";
  result.textContent = "Contacting TruCite backend…";

  // Reset gauge (empty)
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
      const txt = await res.text().catch(() => "");
      throw new Error(`Backend error ${res.status}. ${txt}`);
    }

    const data = await res.json();

    // Accept either score field name
    const rawScore = (data.score ?? data.truth_score ?? 0);
    const score = Math.max(0, Math.min(100, Number(rawScore)));
    const verdict = String(data.verdict ?? verdictFromScore(score));

    // Update UI
    scoreDisplay.textContent = `${score}`;
    scoreVerdict.textContent = verdict;

    // Animate gauge fill
    const dashTotal = 260;
    const filled = (score / 100) * dashTotal;
    const offset = dashTotal - filled;

    if (gaugeFill) {
      setTimeout(() => {
        gaugeFill.style.transition = "stroke-dashoffset 1.1s ease";
        gaugeFill.style.strokeDashoffset = String(offset);
      }, 40);
    }

    // Result box: show full response
    result.textContent = JSON.stringify(data, null, 2);

  } catch (e) {
    scoreDisplay.textContent = "--";
    scoreVerdict.textContent = "Backend connection failed";
    result.textContent =
      "❌ POST failed.\n\n" +
      "Endpoint: " + BACKEND_ENDPOINT + "\n\n" +
      "If you are still running this from Neocities, the likely cause is CORS.\n" +
      "Fix: host the frontend inside Render /static so this becomes same-origin.\n\n" +
      "Error: " + (e?.message || e);
  }
}

function verdictFromScore(score) {
  if (score >= 85) return "Likely True / Well-Supported";
  if (score >= 65) return "Plausible / Needs Verification";
  if (score >= 40) return "Questionable / High Uncertainty";
  return "Likely False / Misleading";
}}
