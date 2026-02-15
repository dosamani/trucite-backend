(() => {
  // ================================
  // TruCite Frontend Script (MVP)
  // Uses /api/score (schema v2.0)
  // ================================

  const CONFIG = {
    API_BASE: "",              // same host
    POLICY_MODE: "enterprise",
    TIMEOUT_MS: 15000
  };

  const $ = (sel) => document.querySelector(sel);
  const byId = (id) => document.getElementById(id);

  function pick(...candidates) {
    for (const c of candidates) {
      if (!c) continue;
      let el = null;
      if (c.startsWith("#") || c.startsWith(".") || c.includes("[") || c.includes(" ")) el = $(c);
      else el = byId(c);
      if (el) return el;
    }
    return null;
  }

  function setText(el, txt) { if (el) el.textContent = txt; }
  function show(el, on = true) { if (el) el.style.display = on ? "" : "none"; }
  function safeJson(obj) { try { return JSON.stringify(obj, null, 2); } catch { return String(obj); } }

  async function fetchWithTimeout(url, options = {}, timeoutMs = 15000) {
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(t);
    }
  }

  function copyToClipboard(text) {
    if (!text) return;
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
    else fallbackCopy(text);
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

  // Elements
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

  const volatilityValue = pick("volatilityValue", "#volatilityValue");
  const policyValue = pick("policyValue", "#policyValue");
  const apiMeta = pick("apiMeta", "#apiMeta");

  let lastPayload = null;
  let lastResponse = null;

  function applyDecisionColor(action) {
    if (!decisionAction) return;
    decisionAction.classList.remove("allow", "review", "block");
    const a = (action || "").toUpperCase();
    if (a === "ALLOW") decisionAction.classList.add("allow");
    else if (a === "BLOCK") decisionAction.classList.add("block");
    else decisionAction.classList.add("review");
  }

  // Gauge animation (stroke-dashoffset)
  function updateGauge(score) {
    if (!gaugeFill) return;
    const s = Math.max(0, Math.min(100, Number(score) || 0));
    const total = 260;

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

    setText(volatilityValue, "—");
    setText(policyValue, "—");
    setText(apiMeta, "server —ms · /api/score");
  }

  function setErrorUI(userMsg, debugText) {
    setText(scoreDisplay, "--");
    setText(scoreVerdict, "Error");
    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, "REVIEW");
    applyDecisionColor("REVIEW");
    setText(decisionReason, userMsg || "Backend error.");
    setText(apiMeta, "server —ms · /api/score");
    if (resultPre) resultPre.textContent = debugText ? `Backend error:\n${debugText}` : (userMsg || "Backend error.");
  }

  function buildDecisionPayload(data) {
    return {
      schema_version: data?.schema_version || "2.0",
      decision: data?.decision || (data?.decision_gate?.action) || "REVIEW",
      score: data?.score ?? "--",
      verdict: data?.verdict || "",
      policy_mode: data?.policy_mode || CONFIG.POLICY_MODE,
      policy_version: data?.policy_version || "",
      policy_hash: data?.policy_hash || "",
      event_id: data?.event_id || "",
      audit_fingerprint_sha256: data?.audit_fingerprint_sha256 || data?.audit_fingerprint?.sha256 || "",
      latency_ms: data?.latency_ms ?? null,
      volatility: data?.volatility || data?.signals?.volatility || "LOW",
      risk_flags: data?.risk_flags || data?.signals?.risk_flags || [],
      guardrail: data?.guardrail || data?.signals?.guardrail || null
    };
  }

  function renderResponse(data) {
    lastResponse = data;

    const score = data?.score ?? "--";
    setText(scoreDisplay, score);
    setText(scoreVerdict, data?.verdict || "");
    updateGauge(score);

    const vol = (data?.volatility || data?.signals?.volatility || "LOW").toString().toUpperCase();
    setText(volatilityValue, vol);

    const pMode = data?.policy_mode || CONFIG.POLICY_MODE;
    const pVer = data?.policy_version || "";
    const pHash = data?.policy_hash || "";
    setText(policyValue, pVer ? `${pMode} v${pVer} (hash: ${pHash})` : `${pMode}`);

    const action = data?.decision || data?.decision_gate?.action || "REVIEW";
    const reason = data?.decision_gate?.reason || (data?.decision_gate?.reason === "" ? "" : null) || data?.decision_gate?.reason || "";
    const gateReason = data?.decision_gate?.reason || data?.decision_gate?.reason; // safe
    const finalReason = data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason;

    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, action);
    applyDecisionColor(action);
    setText(decisionReason, data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || data?.decision_gate?.reason || "");

    const ms = (typeof data?.latency_ms === "number") ? data.latency_ms : "—";
    setText(apiMeta, `server ${ms}ms · /api/score`);

    const decisionPayload = buildDecisionPayload(data);

    const fullText =
      `Decision Payload (live) ${apiMeta?.textContent || ""}\n` +
      `${safeJson(decisionPayload)}\n\n` +
      `Validation details, explanation & references\n` +
      `${safeJson(data)}`;

    if (resultPre) resultPre.textContent = fullText;
  }

  async function onVerify() {
    const text = (claimBox?.value || "").trim();
    const evidence = (evidenceBox?.value || "").trim();

    if (!text) return alert("Paste AI- or agent-generated text first.");

    const payload = { text, evidence: evidence || "", policy_mode: CONFIG.POLICY_MODE };
    lastPayload = payload;
    setPendingUI();

    const url = `${CONFIG.API_BASE}/api/score`;

    try {
      const res = await fetchWithTimeout(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-TruCite-Client": "web-mvp",
          "X-TruCite-Policy-Mode": CONFIG.POLICY_MODE
        },
        body: JSON.stringify(payload)
      }, CONFIG.TIMEOUT_MS);

      if (!res.ok) {
        let t = "";
        try { t = await res.text(); } catch {}
        setErrorUI("could not score. Check backend route and try again.", t || `HTTP ${res.status}`);
        return;
      }

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

  if (verifyButton) verifyButton.addEventListener("click", onVerify);
  else console.warn("VERIFY button not found. Check id/class.");

  // Inline onclick shim (HTML uses onclick="scoreText()")
  window.scoreText = function () {
    if (verifyButton) verifyButton.click();
    else onVerify();
  };

  // Copy helpers
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
    const curl = `curl -X POST "${location.origin}/api/score" -H "Content-Type: application/json" -d '${JSON.stringify(lastPayload)}'`;
    copyToClipboard(curl);
  };

  setPendingUI();

  window.TruCiteDebug = {
    config: CONFIG,
    lastPayload: () => lastPayload,
    lastResponse: () => lastResponse
  };
})();
