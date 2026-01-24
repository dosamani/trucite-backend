async function scoreText() {
  const inputEl = document.getElementById("inputText");
  const evidenceEl = document.getElementById("evidenceText");
  const resultEl = document.getElementById("result");
  const scoreDisplay = document.getElementById("scoreDisplay");
  const scoreVerdict = document.getElementById("scoreVerdict");
  const gaugeFill = document.getElementById("gaugeFill");

  const text = (inputEl?.value || "").trim();
  const evidence = (evidenceEl?.value || "").trim();

  if (!text) {
    scoreDisplay.textContent = "--";
    scoreVerdict.textContent = "Paste text to verify.";
    resultEl.textContent = "";
    setGauge(0);
    return;
  }

  scoreVerdict.textContent = "Verifying…";
  resultEl.textContent = "Calling /verify…";
  scoreDisplay.textContent = "--";
  setGauge(0);

  try {
    const res = await fetch("/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        evidence
        // policy_mode is optional; backend defaults if not supplied.
        // If you later add a selector, include: policy_mode: selectedMode
      })
    });

    if (!res.ok) {
      const errText = await res.text();
      throw new Error(`HTTP ${res.status}: ${errText}`);
    }

    const data = await res.json();

    // Score + verdict
    const score = Number(data?.score ?? 0);
    scoreDisplay.textContent = isFinite(score) ? String(score) : "--";
    setGauge(isFinite(score) ? score : 0);

    // If backend returns a Decision Gate message, show that; otherwise show verdict
    const decision = data?.decision;
    if (decision?.action && decision?.reason) {
      scoreVerdict.textContent = `${decision.action} — ${decision.reason}`;
    } else {
      scoreVerdict.textContent = data?.verdict || "Result returned.";
    }

    // Pretty print the full JSON
    resultEl.textContent = JSON.stringify(data, null, 2);

  } catch (e) {
    scoreDisplay.textContent = "--";
    setGauge(0);
    scoreVerdict.textContent = "Error calling backend.";
    resultEl.textContent = String(e);
  }
}

function setGauge(score) {
  // Gauge arc length is set in HTML as 260.
  const dashTotal = 260;
  const clamped = Math.max(0, Math.min(100, Number(score) || 0));
  const offset = dashTotal - (clamped / 100) * dashTotal;

  const gaugeFill = document.getElementById("gaugeFill");
  if (gaugeFill) {
    gaugeFill.style.strokeDashoffset = String(offset);
  }
}
