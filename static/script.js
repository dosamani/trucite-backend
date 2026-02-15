(() => {
  // ================================
  // TruCite Frontend Script (MVP)
  // Level-2 JSON + volatility gating + backward compatible fallback
  // ================================

  // ---------- CONFIG ----------
  const CONFIG = {
    // If frontend is served from the same host as backend, leave "".
    // If you ever split domains, set: "https://YOUR-BACKEND.onrender.com"
    API_BASE: "",

    // Demo runs enterprise-style policy by default
    POLICY_MODE: "enterprise",

    // Request safety
    TIMEOUT_MS: 15000,

    // Prefer Level-2 endpoint first (falls back to /verify automatically)
    PRIMARY_ENDPOINT: "/api/score",
    FALLBACK_ENDPOINT: "/verify",

    // Client-side failsafe:
    // If backend flags VOLATILE knowledge, do not ALLOW unless evidence is present.
    VOLATILE_REQUIRES_EVIDENCE_FOR_ALLOW: true
  };

  // ---------- Helpers ----------
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

  // Fetch with timeout
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

  function hasEvidence(payload, data) {
    const ev = (payload?.evidence || "").trim();
    if (ev.length >= 3) return true;
    if (data?.signals?.has_references) return true;
    if (data?.references && Array.isArray(data.references) && data.references.length > 0) return true;
    return false;
  }

  // ---------- Element binding ----------
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

  // Optional fields
  const volatilityValue = pick("volatilityValue", "#volatilityValue");
  const policyValue = pick("policyValue", "#policyValue");

  // Level-2 panel (optional, but supported)
  const tcJsonOutput = pick("tcJsonOutput", "#tcJsonOutput");
  const tcLatency = pick("tcLatency", "#tcLatency");

  let lastPayload = null;
  let lastResponse = null;
  let lastEndpointUsed = null;

  // ---------- Decision Styling ----------
  function applyDecisionColor(action) {
    if (!decisionAction) return;
    decisionAction.classList.remove("allow", "review", "block");
    const a = (action || "").toUpperCase();
    if (a === "ALLOW") decisionAction.classList.add("allow");
    else if (a === "BLOCK") decisionAction.classList.add("block");
    else decisionAction.classList.add("review");
  }

  // Gauge uses stroke-dashoffset in your SVG
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

    setText(volatilityValue, "—");
    setText(policyValue, "—");

    if (tcLatency) setText(tcLatency, "—");
    if (tcJsonOutput) tcJsonOutput.textContent = "{}";
    if (resultPre) resultPre.textContent = "";
  }

  function setErrorUI(userMsg, debugText) {
    setText(scoreDisplay, "--");
    setText(scoreVerdict, "Error");
    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, "REVIEW");
    applyDecisionColor("REVIEW");
    setText(decisionReason, userMsg || "Backend error.");

    if (tcLatency) setText(tcLatency, "—");
    if (tcJsonOutput) tcJsonOutput.textContent = safeJson({ error: userMsg || "Backend error." });

    if (resultPre) {
      resultPre.textContent = debugText
        ? `Backend error:\n${debugText}`
        : (userMsg || "Backend error.");
    }
  }

  // Normalize volatility for UI + client failsafe
  function deriveVolatility(data) {
    const guardrail = (data?.signals?.guardrail || "").toString();
    const riskFlags = Array.isArray(data?.signals?.risk_flags) ? data.signals.risk_flags : [];
    const rules = Array.isArray(data?.signals?.rules_fired) ? data.signals.rules_fired : [];

    const volatile =
      guardrail === "volatile_current_fact_no_evidence" ||
      riskFlags.includes("volatile_current_fact_no_evidence") ||
      rules.includes("volatile_current_fact_cap");

    return volatile ? "VOLATILE" : "LOW";
  }

  // Client-side failsafe:
  // If VOLATILE detected, do not allow ALLOW unless evidence is present.
  function applyClientFailSafe(data) {
    try {
      const vol = deriveVolatility(data);
      const action = (data?.decision?.action || "").toString().toUpperCase();
      const hasEv = hasEvidence(lastPayload, data);

      if (CONFIG.VOLATILE_REQUIRES_EVIDENCE_FOR_ALLOW && vol === "VOLATILE" && action === "ALLOW" && !hasEv) {
        data.decision = data.decision || {};
        data.decision.action = "REVIEW";
        data.decision.reason =
          "Volatile knowledge detected (time-sensitive fact). Evidence required for ALLOW under enterprise policy.";
        data.verdict = data.verdict || "Unclear / needs verification";

        data.signals = data.signals || {};
        if (!Array.isArray(data.signals.risk_flags)) data.signals.risk_flags = [];
        if (!data.signals.risk_flags.includes("client_volatility_requires_evidence")) {
          data.signals.risk_flags.push("client_volatility_requires_evidence");
        }
      }
    } catch (e) {
      console.warn("Client failsafe error:", e);
    }
    return data;
  }

  function buildDecisionPayload(data) {
    const payload = {
      schema_version: data?.schema_version,
      decision: data?.decision?.action,
      score: data?.score,
      verdict: data?.verdict,
      policy_mode: data?.policy_mode,
      policy_version: data?.policy_version,
      policy_hash: data?.policy_hash,
      event_id: data?.event_id,
      audit_fingerprint_sha256: data?.audit_fingerprint?.sha256,
      latency_ms: data?.latency_ms,
      volatility: deriveVolatility(data),
      risk_flags: data?.signals?.risk_flags || [],
      guardrail: data?.signals?.guardrail || null
    };

    // Remove undefined keys for cleanliness
    Object.keys(payload).forEach((k) => {
      if (payload[k] === undefined) delete payload[k];
    });

    return payload;
  }

  function renderResponse(data) {
    lastResponse = data;

    // Apply client-side failsafe before rendering
    data = applyClientFailSafe(data);

    const score = data?.score ?? "--";
    setText(scoreDisplay, score);
    setText(scoreVerdict, data?.verdict || "");
    updateGauge(score);

    // Optional: volatility + policy labels
    const vol = deriveVolatility(data);
    setText(volatilityValue, vol);

    const pMode = data?.policy_mode || CONFIG.POLICY_MODE;
    const pVer = data?.policy_version || "";
    const pHash = data?.policy_hash || "";
    const policyLabel = pVer
      ? `${pMode} v${pVer}${pHash ? ` (hash: ${pHash})` : ""}`
      : `${pMode}${pHash ? ` (hash: ${pHash})` : ""}`;
    setText(policyValue, policyLabel);

    const action = data?.decision?.action || "REVIEW";
    const reason = data?.decision?.reason || "";
    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, action);
    applyDecisionColor(action);
    setText(decisionReason, reason);

    // Level-2 panel population (if present)
    const decisionPayload = buildDecisionPayload(data);
    if (tcJsonOutput) tcJsonOutput.textContent = safeJson(decisionPayload);
    if (tcLatency) {
      const server = (typeof data?.latency_ms === "number") ? `${data.latency_ms}ms` : "—";
      const endpoint = lastEndpointUsed || CONFIG.PRIMARY_ENDPOINT;
      setText(tcLatency, `server ${server} · ${endpoint}`);
    }

    // Full details output stays in the original details block
    if (resultPre) resultPre.textContent = safeJson(data);
  }

  async function postToEndpoint(endpoint, payload) {
    const url = `${CONFIG.API_BASE}${endpoint}`;
    return await fetchWithTimeout(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-TruCite-Client": "web-mvp",
        "X-TruCite-Policy-Mode": CONFIG.POLICY_MODE
      },
      body: JSON.stringify(payload)
    }, CONFIG.TIMEOUT_MS);
  }

  async function onVerify() {
    const text = (claimBox?.value || "").trim();
    const evidence = (evidenceBox?.value || "").trim();

    if (!text) return alert("Paste AI- or agent-generated text first.");

    const payload = { text, evidence: evidence || "", policy_mode: CONFIG.POLICY_MODE };
    lastPayload = payload;
    setPendingUI();

    try {
      // Try Level-2 endpoint first
      let res = await postToEndpoint(CONFIG.PRIMARY_ENDPOINT, payload);
      lastEndpointUsed = CONFIG.PRIMARY_ENDPOINT;

      // If endpoint missing or method mismatch, fall back to /verify
      if (!res.ok && (res.status === 404 || res.status === 405 || res.status === 501)) {
        res = await postToEndpoint(CONFIG.FALLBACK_ENDPOINT, payload);
        lastEndpointUsed = CONFIG.FALLBACK_ENDPOINT;
      }

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

  // Attach handler
  if (verifyButton) verifyButton.addEventListener("click", onVerify);
  else console.warn("VERIFY button not found. Check id/class.");

  // Inline onclick compatibility shim
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
    // Copy the infra-grade decision payload if present, else full response
    const toCopy = tcJsonOutput ? (tcJsonOutput.textContent || safeJson(lastResponse)) : safeJson(lastResponse);
    copyToClipboard(toCopy);
  };

  window.copyCurl = function () {
    if (!lastPayload) return alert("Run a verification first.");
    const endpoint = lastEndpointUsed || CONFIG.PRIMARY_ENDPOINT;
    const base = CONFIG.API_BASE ? CONFIG.API_BASE : location.origin;

    const curl =
      `curl -X POST "${base}${endpoint}" ` +
      `-H "Content-Type: application/json" ` +
      `-H "X-TruCite-Policy-Mode: ${CONFIG.POLICY_MODE}" ` +
      `-d '${JSON.stringify(lastPayload)}'`;

    copyToClipboard(curl);
  };

  // Start state
  setPendingUI();

  // Optional debug hook
  window.TruCiteDebug = {
    config: CONFIG,
    elements: {
      verifyButton, claimBox, evidenceBox, scoreDisplay, scoreVerdict,
      gaugeFill, decisionCard, decisionAction, decisionReason, resultPre,
      volatilityValue, policyValue, tcJsonOutput, tcLatency
    },
    lastPayload: () => lastPayload,
    lastResponse: () => lastResponse,
    lastEndpointUsed: () => lastEndpointUsed
  };
})();
