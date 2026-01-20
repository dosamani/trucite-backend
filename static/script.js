async function scoreText() {
  const inputEl = document.getElementById("inputText");
  const evidenceEl = document.getElementById("evidenceText");
  const resultEl = document.getElementById("result");

  const scoreDisplay = document.getElementById("scoreDisplay");
  const scoreVerdict = document.getElementById("scoreVerdict");
  const gaugeFill = document.getElementById("gaugeFill");

  const claimsSection = document.getElementById("claimsSection");
  const claimsBody = document.getElementById("claimsBody");
  const driftLine = document.getElementById("driftLine");

  const text = (inputEl.value || "").trim();
  const evidence = (evidenceEl?.value || "").trim();

  if (!text) {
    alert("Paste some AI output first.");
    return;
  }

  // UI reset
  resultEl.textContent = "Scoring...";
  scoreDisplay.textContent = "--";
  scoreVerdict.textContent = "Scoring…";
  gaugeFill.style.strokeDashoffset = "260";
  claimsSection.style.display = "none";
  claimsBody.innerHTML = "";
  driftLine.textContent = "";

  try {
    const resp = await fetch("/api/score", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, evidence })
    });

    if (!resp.ok) {
      const t = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${t}`);
    }

    const data = await resp.json();

    // Gauge update
    const score = Number(data.score ?? 0);
    const clamped = Math.max(0, Math.min(100, score));
    const dash = 260 - (260 * clamped / 100);
    gaugeFill.style.strokeDashoffset = String(dash);

    scoreDisplay.textContent = clamped.toFixed(0);
    scoreVerdict.textContent = data.verdict || "—";

    // Claim-level table
    if (Array.isArray(data.claims) && data.claims.length > 0) {
      claimsSection.style.display = "block";

      if (data.drift) {
        const d = data.drift;
        if (d.has_prior) {
          driftLine.textContent =
            `Drift: prior=${d.prior_timestamp_utc}, score_delta=${d.score_delta}, ` +
            `verdict_changed=${d.verdict_changed}, flag=${d.drift_flag}`;
        } else {
          driftLine.textContent = "Drift: no prior run for this input.";
        }
      }

      data.claims.forEach((c, idx) => {
        const tr = document.createElement("tr");

        const tdIdx = document.createElement("td");
        tdIdx.textContent = String(idx + 1);

        const tdClaim = document.createElement("td");
        tdClaim.textContent = c.text || "";

        const tdScore = document.createElement("td");
        tdScore.textContent = String(c.score ?? "");

        const tdVerdict = document.createElement("td");
        tdVerdict.textContent = c.verdict || "";

        const tdTags = document.createElement("td");
        tdTags.textContent = (c.risk_tags && c.risk_tags.length) ? c.risk_tags.join(", ") : "-";

        tr.appendChild(tdIdx);
        tr.appendChild(tdClaim);
        tr.appendChild(tdScore);
        tr.appendChild(tdVerdict);
        tr.appendChild(tdTags);

        claimsBody.appendChild(tr);
      });
    }

    resultEl.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    resultEl.textContent = `Error communicating with TruCite engine:\n${err.message}`;
    scoreVerdict.textContent = "Engine error";
  }
}
