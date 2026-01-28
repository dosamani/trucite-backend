(() => {
  // ---------- Helpers ----------
  const $ = (sel) => document.querySelector(sel);
  const byId = (id) => document.getElementById(id);

  function pick(...candidates) {
    for (const c of candidates) {
      if (!c) continue;
      let el = null;
      if (c.startsWith("#") || c.startsWith(".") || c.includes("[") || c.includes(" ")) {
        el = $(c);
      } else {
        el = byId(c);
      }
      if (el) return el;
    }
    return null;
  }

  function setText(el, txt) { if (el) el.textContent = txt; }
  function show(el, on = true) { if (el) el.style.display = on ? "" : "none"; }
  function safeJson(obj) { try { return JSON.stringify(obj, null, 2); } catch { return String(obj); } }

  function copyToClipboard(text) {
    if (!text) return;
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
    } else fallbackCopy(text);
  }

  function fallbackCopy(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch {}
    document.body.removeChild(ta);
  }

  // ---------- Element binding ----------
  // Robust: find VERIFY button by id or class; fallback to button with text "VERIFY"
  let verifyButton = pick("verifyBtn", "#verifyBtn", "button.primary-btn");
  if (!verifyButton) {
    const allBtns = Array.from(document.querySelectorAll("button"));
    verifyButton = allBtns.find(b => (b.textContent || "").trim().toUpperCase() === "VERIFY") || null;
  }

  const claimBox = pick("inputText", "#inputText", "textarea");
  const evidenceBox = pick("evidenceText", "#evidenceText");
  const scoreDisplay = pick("scoreDisplay", "#scoreDisplay");
  const scoreVerdict = pick("scoreVerdict", "#scoreVerdict");
  const gaugeFill = pick("gaugeFill", "#gaugeFill");
  const decisionCard = pick(".decision-card", "decisionBox", "#decisionBox");
  const decisionAction = pick("decisionAction", "#decisionAction");
  const decisionReason = pick("decisionReason", "#decisionReason");
  const resultPre = pick("result", "#result");

  let lastPayload = null;
  let lastResponse = null;

  // ---------- Decision Styling ----------
  function applyDecisionColor(action) {
    if (!decisionAction) return;
    decisionAction.classList.remove("allow", "review", "block");
    const a = (action || "").toUpperCase();
    if (a === "ALLOW") decisionAction.classList.add("allow");
    else if (a === "BLOCK") decisionAction.classList.add("block");
    else decisionAction.classList.add("review");
  }

  // Gauge uses stroke-dashoffset in your SVG (best for your current HTML/CSS)
  function updateGauge(score) {
    if (!gaugeFill) return;
    const s = Math.max(0, Math.min(100, Number(score) || 0));
    const total = 260; // matches dasharray in HTML
    gaugeFill.style.strokeDashoffset = total - (s / 100) * total;
  }

  function setPendingUI() {
    setText(scoreDisplay, "--");
    setText(scoreVerdict, "Score pending…");
    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, "—");
    setText(decisionReason, "Awaiting verification…");
    updateGauge(0);
    if (resultPre) resultPre.textContent = "";
  }

  function setErrorUI(userMsg, debugText) {
    setText(scoreDisplay, "--");
    setText(scoreVerdict, "Error");
    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, "REVIEW");
    applyDecisionColor("REVIEW");
    setText(decisionReason, userMsg || "Backend error.");
    if (resultPre) {
      resultPre.textContent = debugText ? `Backend error:\n${debugText}` : (userMsg || "Backend error.");
    }
  }

  function renderResponse(data) {
    lastResponse = data;

    const score = data?.score ?? "--";
    setText(scoreDisplay, score);
    setText(scoreVerdict, data?.verdict || "");
    updateGauge(score);

    const action = data?.decision?.action || "REVIEW";
    const reason = data?.decision?.reason || "";
    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, action);
    applyDecisionColor(action);
    setText(decisionReason, reason);

    if (resultPre) resultPre.textContent = safeJson(data);
  }

  async function onVerify() {
    const text = (claimBox?.value || "").trim();
    const evidence = (evidenceBox?.value || "").trim();

    if (!text) return alert("Paste AI- or agent-generated text first.");

    const payload = { text, evidence: evidence || "", policy_mode: "enterprise" };
    lastPayload = payload;
    setPendingUI();

    try {
      const res = await fetch("/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      // Handle non-OK errors gracefully
      if (!res.ok) {
        let t = "";
        try { t = await res.text(); } catch {}
        setErrorUI("could not score. Check backend route and try again.", t || `HTTP ${res.status}`);
        return;
      }

      // Parse JSON safely
      let data = null;
      try {
        data = await res.json();
      } catch (e) {
        const txt = await res.text().catch(() => "");
        setErrorUI("could not parse response JSON.", txt || String(e));
        return;
      }

      renderResponse(data);
    } catch (e) {
      setErrorUI("could not score. Network or backend unavailable.", String(e));
    }
  }

  // Attach handler
  if (verifyButton) verifyButton.addEventListener("click", onVerify);
  else console.warn("VERIFY button not found. Check id/class.");

  // ✅ IMPORTANT: Inline onclick compatibility shim.
  // Your HTML currently uses onclick="scoreText()". This ensures it always works.
  window.scoreText = function () {
    if (verifyButton) verifyButton.click();
    else onVerify();
  };

  // ---------- COPY FUNCTIONS ----------
  window.copyJSONPayload = function () {
    if (!lastPayload) return alert("Run a verification first.");
    copyToClipboard(safeJson(lastPayload));
  };

  window.copyResponse = function () {
    if (!lastResponse) return alert("Run a verification first.");
    copyToClipboard(safeJson(lastResponse));
  };

  window.copyCurl = function () {
    if (!lastPayload) return alert("Run a verification first.");
    const curl = `curl -X POST "${location.origin}/verify" -H "Content-Type: application/json" -d '${JSON.stringify(lastPayload)}'`;
    copyToClipboard(curl);
  };

  // Start state
  setPendingUI();

  // Optional debug hook
  window.TruCiteDebug = {
    elements: { verifyButton, claimBox, evidenceBox, scoreDisplay, scoreVerdict, gaugeFill, decisionCard, decisionAction, decisionReason, resultPre },
    lastPayload: () => lastPayload,
    lastResponse: () => lastResponse
  };
})();
