(() => {
  // ================================
  // TruCite Frontend Script (MVP)
  // Uses POST /api/score (contract v2.0+)
  // UI language prefers "readiness_signal" over "score"
  // ================================

  const CONFIG = {
    API_BASE: "",              // same host
    POLICY_MODE: "enterprise", // enterprise | health | legal | finance
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

  // UI labels that used to show "score"
  // We keep the IDs but treat the value as "readiness_signal"
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
  function updateGauge(signal) {
    if (!gaugeFill) return;
    const s = Math.max(0, Math.min(100, Number(signal) || 0));
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

  function getReadinessSignal(data) {
    // Prefer readiness_signal (new), fall back to score (legacy)
    const v = (data?.readiness_signal ?? data?.readinessSignal ?? data?.score ?? null);
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }

  function setPendingUI() {
    setText(scoreDisplay, "--");
    setText(scoreVerdict, "Readiness pending…");
    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, "—");
    setText(decisionReason, "Awaiting evaluation…");
    updateGauge(0);
    if (resultPre) resultPre.textContent = "";

    setText(volatilityValue, "—");
    setText(policyValue, "—");
    // Remove endpoint from the status line (you called this out)
    setText(apiMeta, "runtime gate · server —ms");
  }

  function setErrorUI(userMsg, debugText) {
    setText(scoreDisplay, "--");
    setText(scoreVerdict, "Error");
    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, "REVIEW");
    applyDecisionColor("REVIEW");
    setText(decisionReason, userMsg || "Backend error.");
    setText(apiMeta, "runtime gate · server —ms");
    if (resultPre) resultPre.textContent = debugText ? `Backend error:\n${debugText}` : (userMsg || "Backend error.");
  }
  // Normalizes decision fields across:
  // - decision: {action, reason}
  // - decision: "ALLOW" + decision_detail: {reason}
  function normalizeDecision(data) {
    const rawDecision = data?.decision;

    if (rawDecision && typeof rawDecision === "object") {
      return {
        action: rawDecision.action || "REVIEW",
        reason: rawDecision.reason || ""
      };
    }

    const action = (typeof rawDecision === "string" ? rawDecision : null) || "REVIEW";
    const reason =
      data?.decision_detail?.reason ||
      data?.decision_reason ||
      data?.reason ||
      data?.decision_detail?.message ||
      "";

    return { action, reason };
  }

  function buildDecisionPayload(data) {
    const sig = data?.signals || {};
    const decisionObj = normalizeDecision(data);

    const volatility = (sig?.volatility ?? data?.volatility ?? "STABLE");
    const volatilityCategory =
      (sig?.volatility_category ??
       data?.volatility_category ??
       data?.volatilityCategory ??
       "");

    const evidenceStatus =
      (sig?.evidence_validation_status ??
       data?.evidence_validation_status ??
       "NONE");

    const evidenceTrustTier =
      (sig?.evidence_trust_tier ??
       data?.evidence_trust_tier ??
       "C");

    const evidenceConfidence =
      (typeof sig?.evidence_confidence === "number") ? sig.evidence_confidence :
      (typeof data?.evidence_confidence === "number") ? data.evidence_confidence :
      null;

    // Policy fallbacks (demo response uses data.policy.*)
    const pMode = data?.policy_mode || data?.policy?.mode || CONFIG.POLICY_MODE;
    const pVer  = data?.policy_version || data?.policy?.version || "";
    const pHash = data?.policy_hash || data?.policy?.hash || "";

    // Audit fallbacks (demo response uses data.audit.*)
    const eventId = data?.event_id || data?.audit?.event_id || data?.request_id || data?.contract?.request_id || null;
    const auditSha =
      data?.audit_fingerprint_sha256 ||
      data?.audit?.audit_fingerprint_sha256 ||
      data?.audit_fingerprint?.sha256 ||
      "";

    const ms = (typeof data?.latency_ms === "number") ? data.latency_ms : null;

    const readiness = getReadinessSignal(data);

    return {
      schema_version: data?.schema_version || data?.contract?.schema_version || "2.0",
      request_id: data?.request_id || data?.contract?.request_id || eventId || null,

      // Canonical enforcement output
      decision: (decisionObj.action || "REVIEW").toUpperCase(),

      // Preferred naming (anti-"scoring")
      readiness_signal: readiness,

      verdict: data?.verdict || "",

      policy_mode: pMode,
      policy_version: pVer,
      policy_hash: pHash,

      event_id: eventId,
      audit_fingerprint_sha256: auditSha,

      latency_ms: ms,

      volatility: String(volatility).toUpperCase(),
      volatility_category: volatilityCategory,

      evidence_validation_status: evidenceStatus,
      evidence_trust_tier: evidenceTrustTier,
      evidence_confidence: evidenceConfidence,

      risk_flags: sig?.risk_flags || data?.risk_flags || [],
      guardrail: sig?.guardrail ?? data?.guardrail ?? null,

      execution_boundary: data?.execution_boundary ?? false,
      execution_commit: data?.execution_commit ?? {
        authorized: false,
        action: null,
        event_id: null,
        policy_hash: null,
        audit_fingerprint_sha256: null
      }
    };
  }

  function renderResponse(data) {
    lastResponse = data;

    const readiness = getReadinessSignal(data);

    setText(scoreDisplay, String(readiness));
    setText(scoreVerdict, data?.verdict || "");
    updateGauge(readiness);

    const sig = data?.signals || {};

    // Volatility
    const vol = (sig?.volatility ?? data?.volatility ?? "STABLE").toString().toUpperCase();
    setText(volatilityValue, vol);

    // Policy label
    const pMode = data?.policy_mode || data?.policy?.mode || CONFIG.POLICY_MODE;
    const pVer  = data?.policy_version || data?.policy?.version || "";
    const pHash = data?.policy_hash || data?.policy?.hash || "";
    const policyLabel = pVer ? `${pMode} v${pVer}` + (pHash ? ` (hash: ${pHash})` : "") : `${pMode}`;
    setText(policyValue, policyLabel);

    // ---- Execution Commit (downstream enforcement artifact) ----
    const exec = data?.execution_commit || data?.executionCommit || null;

    const execCard = document.getElementById("execCommitCard");
    const execBoundary = document.getElementById("execBoundary");
    const execAuthorized = document.getElementById("execAuthorized");
    const execAction = document.getElementById("execAction");
    const execEventId = document.getElementById("execEventId");
    const execPolicyHash = document.getElementById("execPolicyHash");
    const execAudit = document.getElementById("execAudit");

    // Always reflect execution boundary (top-level)
    if (execBoundary) {
      const boundary = data?.execution_boundary === true;
      execBoundary.textContent = boundary ? "TRUE" : "FALSE";
      execBoundary.style.color = boundary ? "#10b981" : "#ef4444";
      execBoundary.style.fontWeight = "700";
    }

    if (exec && exec.authorized !== undefined) {
      if (execCard) execCard.style.display = "block";

      const authorized = exec.authorized === true;

      if (execAuthorized) {
        execAuthorized.textContent = authorized ? "YES" : "NO";
        execAuthorized.style.color = authorized ? "#10b981" : "#ef4444";
        execAuthorized.style.fontWeight = "700";
      }

      if (execAction) execAction.textContent = exec.action || "—";
      if (execEventId) execEventId.textContent = exec.event_id || data?.audit?.event_id || data?.event_id || "—";
      if (execPolicyHash) execPolicyHash.textContent = exec.policy_hash || pHash || "—";

      if (execAudit) {
        execAudit.textContent =
          exec.audit_fingerprint_sha256 ||
          data?.audit_fingerprint_sha256 ||
          data?.audit?.audit_fingerprint_sha256 ||
          data?.audit_fingerprint?.sha256 ||
          "—";
      }
    } else {
      if (execCard) execCard.style.display = "none";
    }

    // Decision normalization
    const decisionObj = normalizeDecision(data);
    const action = (decisionObj.action || "REVIEW").toUpperCase();
    const reason = decisionObj.reason || "";

    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, action);
    applyDecisionColor(action);
    setText(decisionReason, reason);

    const ms = (typeof data?.latency_ms === "number") ? data.latency_ms : "—";
    // Remove endpoint from the status line (you called this out)
    setText(apiMeta, `runtime gate · server ${ms}ms`);

    const decisionPayload = buildDecisionPayload(data);

    const fullText =
      `Execution Decision Artifact (live)\n` +
      `Latency: ${ms}ms\n\n` +
      `${safeJson(decisionPayload)}\n\n` +
      `Validation details, explanation & references\n` +
      `${safeJson(data)}`;

    if (resultPre) resultPre.textContent = fullText;
  }
  async function onVerify() {
    const text = (claimBox?.value || "").trim();
    const evidence = (evidenceBox?.value || "").trim();

    if (!text) return alert("Paste AI- or agent-generated text first.");

    const payload = {
      text,
      evidence: evidence || "",
      policy_mode: CONFIG.POLICY_MODE
    };

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
        setErrorUI("could not evaluate. Check backend route and try again.", t || `HTTP ${res.status}`);
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

      // If backend returns structured error, show cleanly
      if (data?.error_code) {
        setErrorUI(data?.message || "Request error.", safeJson(data));
        return;
      }

      renderResponse(data);
    } catch (e) {
      const msg = (String(e || "").includes("AbortError"))
        ? "Request timed out. Backend may be waking up. Try again."
        : "could not evaluate. Network or backend unavailable.";
      setErrorUI(msg, String(e));
    }
  }

  if (verifyButton) verifyButton.addEventListener("click", onVerify);
  else console.warn("VERIFY button not found. Check id/class.");

  // Inline onclick shim (HTML uses onclick="scoreText()")
  // Keep name for compatibility, but internally it "evaluates"
  window.scoreText = function () {
    if (verifyButton) verifyButton.click();
    else onVerify();
  };

  // Copy helpers
  window.copyJSONPayload = function () {
    if (!lastPayload) return alert("Run an evaluation first.");
    copyToClipboard(safeJson(lastPayload));
  };

  window.copyResponse = function () {
    if (!lastResponse) return alert("Run an evaluation first.");
    copyToClipboard(safeJson(lastResponse));
  };

  window.copyCurl = function () {
    if (!lastPayload) return alert("Run an evaluation first.");
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
