async function scoreText() {
  const input = document.getElementById("inputText");
  const result = document.getElementById("result");
  const scoreDisplay = document.getElementById("scoreDisplay");
  const scoreVerdict = document.getElementById("scoreVerdict");
  const gaugeFill = document.getElementById("gaugeFill");

  // Optional UI: claim table container (we create if missing)
  let claimTable = document.getElementById("claimTable");

  if (!input || !result || !scoreDisplay || !scoreVerdict || !gaugeFill) {
    console.error("Missing required elements. Check IDs in index.html.");
    return;
  }

  const text = (input.value || "").trim();
  if (!text) {
    result.textContent = "Please paste AI output to verify.";
    return;
  }

  result.textContent = "Analyzing…";
  scoreDisplay.textContent = "--";
  scoreVerdict.textContent = "Score pending…";
  gaugeFill.style.strokeDashoffset = "260";

  // Create claim table container once (inject right above the JSON result box)
  if (!claimTable) {
    claimTable = document.createElement("div");
    claimTable.id = "claimTable";
    claimTable.style.maxWidth = "820px";
    claimTable.style.margin = "22px auto 0";
    claimTable.style.textAlign = "left";
    // Insert before result wrapper
    const wrapper = document.querySelector(".result-wrapper");
    if (wrapper && wrapper.parentNode) {
      wrapper.parentNode.insertBefore(claimTable, wrapper);
    }
  }
  claimTable.innerHTML = "";

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

    // Render claim-level summary (new)
    renderClaimTable(claimTable, data);

    // Raw JSON output (keep)
    result.textContent = JSON.stringify(data, null, 2);

  } catch (e) {
    console.error(e);
    result.textContent = "Error communicating with TruCite engine.";
    scoreVerdict.textContent = "Error";
  }
}

function renderClaimTable(container, data) {
  const claims = (data && data.claims) ? data.claims : [];
  const drift = data && data.drift ? data.drift : null;

  const header = document.createElement("div");
  header.style.marginBottom = "10px";
  header.style.color = "#FFD700";
  header.style.fontWeight = "900";
  header.textContent = "Claim-level scoring (MVP)";
  container.appendChild(header);

  if (drift) {
    const d = document.createElement("div");
    d.style.marginBottom = "10px";
    d.style.color = "#ccc";
    d.style.fontSize = "0.92rem";

    let driftLine = "Drift: ";
    if (!drift.has_prior) {
      driftLine += "no prior run for this input.";
    } else {
      driftLine += `prior=${drift.prior_timestamp_utc}, score_delta=${drift.score_delta}, verdict_changed=${drift.verdict_changed}, flag=${drift.drift_flag}`;
    }
    d.textContent = driftLine;
    container.appendChild(d);
  }

  if (!claims.length) {
    const empty = document.createElement("div");
    empty.style.color = "#ccc";
    empty.textContent = "No claims extracted.";
    container.appendChild(empty);
    return;
  }

  const table = document.createElement("table");
  table.style.width = "100%";
  table.style.borderCollapse = "collapse";
  table.style.background = "rgba(0,0,0,0.4)";
  table.style.border = "1px solid rgba(255,215,0,0.25)";
  table.style.borderRadius = "12px";
  table.style.overflow = "hidden";

  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  ["#", "Claim", "Score", "Verdict", "Risk tags"].forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h;
    th.style.textAlign = "left";
    th.style.padding = "10px";
    th.style.fontSize = "0.85rem";
    th.style.color = "#FFD700";
    th.style.borderBottom = "1px solid rgba(255,215,0,0.18)";
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");

  claims.forEach((c, i) => {
    const tr = document.createElement("tr");
    tr.style.borderBottom = "1px solid rgba(255,215,0,0.12)";

    const tdIdx = document.createElement("td");
    tdIdx.textContent = String(i + 1);
    tdIdx.style.padding = "10px";
    tdIdx.style.color = "#ccc";
    tdIdx.style.verticalAlign = "top";

    const tdClaim = document.createElement("td");
    tdClaim.textContent = c.text || "";
    tdClaim.style.padding = "10px";
    tdClaim.style.color = "#fff";
    tdClaim.style.verticalAlign = "top";

    const tdScore = document.createElement("td");
    tdScore.textContent = String(c.score ?? "--");
    tdScore.style.padding = "10px";
    tdScore.style.color = "#FFD700";
    tdScore.style.fontWeight = "900";
    tdScore.style.verticalAlign = "top";

    const tdVerdict = document.createElement("td");
    tdVerdict.textContent = c.verdict || "--";
    tdVerdict.style.padding = "10px";
    tdVerdict.style.color = "#ccc";
    tdVerdict.style.verticalAlign = "top";

    const tdTags = document.createElement("td");
    tdTags.textContent = (c.risk_tags && c.risk_tags.length) ? c.risk_tags.join(", ") : "-";
    tdTags.style.padding = "10px";
    tdTags.style.color = "#ccc";
    tdTags.style.verticalAlign = "top";

    tr.appendChild(tdIdx);
    tr.appendChild(tdClaim);
    tr.appendChild(tdScore);
    tr.appendChild(tdVerdict);
    tr.appendChild(tdTags);
    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  container.appendChild(table);
}
