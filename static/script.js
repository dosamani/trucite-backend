(() => {
  // ================================
  // TruCite Frontend Script (MVP)
  // Step 3/4 Updates: headers + config + volatility gating (client-side failsafe)
  // ================================

  // ---------- CONFIG ----------
  const CONFIG = {
    // If your frontend is served from the same host as backend, leave "".
    // If you ever split domains, set: "https://YOUR-BACKEND.onrender.com"
    API_BASE: "",

    // Demo runs enterprise-style policy by default
    POLICY_MODE: "enterprise",

    // Request safety
    TIMEOUT_MS: 15000,

    // Client-side failsafe:
    // If backend flags a claim as VOLATILE knowledge, do not ALLOW unless evidence is present.
    VOLATILE_REQUIRES_EVIDENCE_FOR_ALLOW: true
  };

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

  // Fetch with timeout
  async function fetchWithTimeout(url, options = {}, timeoutMs = 15000) {
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, { ...options, signal: controller.signal });
      return res;
    } finally {
      clearTimeout(t);
    }
  }

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

  // Optional: If you add these elements in HTML later, JS will populate them.
  const volatilityValue = pick("volatilityValue", "#volatilityValue");
  const policyValue = pick("policyValue", "#policyValue");

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
  const total = 260;

  // Reset first so animation plays every time
  gaugeFill.style.transition = "none";
  gaugeFill.style.strokeDashoffset = total;

  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      gaugeFill.style.transition = "stroke-dashoffset 0.9s cubic-bezier(0.4, 0, 0.2, 1)";
      gaugeFill.style.strokeDashoffset = total - (s / 100) * total;
    });
  });
  }

  function setPendingUI() {
    setText(scoreDisplay, "--");
    setText(scoreVerdict, "Score pending…");
    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, "—");
    setText(decisionReason, "Awaiting verification…");
    updateGauge(0);
    if (resultPre) resultPre.textContent = "";

    // Optional fields
    setText(volatilityValue, "—");
    setText(policyValue, "—");
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

  // Client-side failsafe policy adjustment:
  // If VOLATILE knowledge is detected, do not allow ALLOW unless evidence is present.
  function applyClientFailSafe(data) {
    try {
      const vol = (data?.signals?.knowledge_volatility || data?.signals?.volatility || "").toString().toUpperCase();
      const action = (data?.decision?.action || "").toString().toUpperCase();
      const hasEvidence =
        !!(lastPayload?.evidence && String(lastPayload.evidence).trim().length > 0) ||
        !!(data?.signals?.has_evidence);

      if (CONFIG.VOLATILE_REQUIRES_EVIDENCE_FOR_ALLOW && vol === "VOLATILE" && action === "ALLOW" && !hasEvidence) {
        // Mutate response for UI purposes (keeps JSON visible to user)
        data.decision = data.decision || {};
        data.decision.action = "REVIEW";
        data.decision.reason =
          "Knowledge volatility detected (time-sensitive fact). Evidence required for ALLOW in enterprise mode.";
        data.verdict = data.verdict || "Unclear / needs verification";
        // Add a visible flag
        data.signals = data.signals || {};
        if (!Array.isArray(data.signals.risk_flags)) data.signals.risk_flags = [];
        if (!data.signals.risk_flags.includes("knowledge_volatility_requires_evidence")) {
          data.signals.risk_flags.push("knowledge_volatility_requires_evidence");
        }
      }
    } catch (e) {
      // never crash UI
      console.warn("Client failsafe error:", e);
    }
    return data;
  }

  function renderResponse(data) {
    lastResponse = data;

    // Apply client-side failsafe *before* showing action
    data = applyClientFailSafe(data);

    const score = data?.score ?? "--";
    setText(scoreDisplay, score);
    setText(scoreVerdict, data?.verdict || "");
    updateGauge(score);

    // Optional: policy/volatility labels (won't break if HTML not present)
    const vol = data?.signals?.knowledge_volatility || data?.signals?.volatility || "LOW";
    setText(volatilityValue, String(vol).toUpperCase());
    const pMode = data?.policy_mode || CONFIG.POLICY_MODE;
    const pVer = data?.policy_version || "";
    setText(policyValue, pVer ? `${pMode} v${pVer}` : `${pMode}`);

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

    const payload = { text, evidence: evidence || "", policy_mode: CONFIG.POLICY_MODE };
    lastPayload = payload;
    setPendingUI();

    // Build URL
    const url = `${CONFIG.API_BASE}/verify`;

    try {
      const res = await fetchWithTimeout(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // Optional headers for enterprise-style observability (backend can ignore safely)
          "X-TruCite-Client": "web-mvp",
          "X-TruCite-Policy-Mode": CONFIG.POLICY_MODE
        },
        body: JSON.stringify(payload)
      }, CONFIG.TIMEOUT_MS);

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
      const msg = (String(e || "").includes("AbortError"))
        ? "Request timed out. Backend may be waking up. Try again."
        : "could not score. Network or backend unavailable.";
      setErrorUI(msg, String(e));
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
    config: CONFIG,
    elements: { verifyButton, claimBox, evidenceBox, scoreDisplay, scoreVerdict, gaugeFill, decisionCard, decisionAction, decisionReason, resultPre, volatilityValue, policyValue },
    lastPayload: () => lastPayload,
    lastResponse: () => lastResponse
  };
})();
