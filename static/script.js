(() => {
  // ================================
  // TruCite Frontend Script (MVP)
  // Safe public output (no IP leakage)
  // Auto-fallback: /api/score then /api/runtime
  // ================================

  const CONFIG = {
    API_BASE: "https://trucite-backend.onrender.com",
    POLICY_MODE: "enterprise", // enterprise | health | legal | finance
    TIMEOUT_MS: 15000,

    // ---- Leakage controls ----
    SHOW_DEBUG: false,          // if true, show minimal debug text (never raw objects)
    SHOW_COPY_PAYLOAD: true,    // keep if you want
    SHOW_COPY_CURL: true,       // keep if you want
    SHOW_COPY_RESPONSE: true    // copies sanitized public contract only
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

  function stripHtml(raw) {
    if (!raw) return "";
    // If backend returns HTML error pages, don’t render them into the product UI.
    const s = String(raw);
    if (s.includes("<!doctype") || s.includes("<html") || s.includes("<title>")) return "[backend returned HTML error body]";
    return s;
  }

  // ----------------------------
  // Elements
  // ----------------------------
  let verifyButton = pick("verifyBtn", "#verifyBtn", "button.primary-btn");
  if (!verifyButton) {
    const allBtns = Array.from(document.querySelectorAll("button"));
    verifyButton = allBtns.find(b => (b.textContent || "").trim().toUpperCase() === "VERIFY") || null;
  }

  const claimBox = pick("inputText", "#inputText", "textarea");
  const evidenceBox = pick("evidenceText", "#evidenceText");

  // NOTE: keep existing IDs; UI text is what matters
  const scoreDisplay = pick("scoreDisplay", "#scoreDisplay");   // displays readiness_signal
  const scoreVerdict = pick("scoreVerdict", "#scoreVerdict");
  const gaugeFill = pick("gaugeFill", "#gaugeFill");

  const decisionCard = pick(".decision-card", "decisionBox", "#decisionBox");
  const decisionAction = pick("decisionAction", "#decisionAction");
  const decisionReason = pick("decisionReason", "#decisionReason");

  const resultPre = pick("result", "#result");

  const volatilityValue = pick("volatilityValue", "#volatilityValue");
  const policyValue = pick("policyValue", "#policyValue");
  const apiMeta = pick("apiMeta", "#apiMeta");

  // Track last successful endpoint for curl copying
  let lastPayload = null;
  let lastResponsePublic = null;     // sanitized public contract
  let lastEndpointPath = null;       // "/api/score" or "/api/runtime"

  function applyDecisionColor(action) {
    if (!decisionAction) return;
    decisionAction.classList.remove("allow", "review", "block");
    const a = (action || "").toUpperCase();
    if (a === "ALLOW") decisionAction.classList.add("allow");
    else if (a === "BLOCK") decisionAction.classList.add("block");
    else decisionAction.classList.add("review");
  }

  // Gauge animation (stroke-dashoffset)
  function updateGauge(val) {
    if (!gaugeFill) return;
    const s = Math.max(0, Math.min(100, Number(val) || 0));
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
    setText(scoreVerdict, "Signal pending…");
    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, "—");
    setText(decisionReason, "Awaiting evaluation…");
    updateGauge(0);
    if (resultPre) resultPre.textContent = "";

    setText(volatilityValue, "—");
    setText(policyValue, "—");
    setText(apiMeta, "runtime gate · server —ms");

    // Hide execution commit until we have one
    const execCard = document.getElementById("execCommitCard");
    if (execCard) execCard.style.display = "none";
  }

  function setErrorUI(userMsg, debugText) {
    setText(scoreDisplay, "--");
    setText(scoreVerdict, "Error");
    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, "REVIEW");
    applyDecisionColor("REVIEW");
    setText(decisionReason, userMsg || "Backend error.");
    setText(apiMeta, "runtime gate · server —ms");

    const dbg = CONFIG.SHOW_DEBUG ? stripHtml(debugText) : "";
    if (resultPre) resultPre.textContent = dbg ? `Backend error:\n${dbg}` : (userMsg || "Backend error.");
  }

  function normalizeDecision(data) {
    const rawDecision = data?.decision;
    if (rawDecision && typeof rawDecision === "object") {
      return { action: rawDecision.action || "REVIEW", reason: rawDecision.reason || "" };
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

  // ---- Public (sanitized) contract ----
  // This is what we display + allow copying.
  function buildPublicContract(data) {
    const sig = data?.signals || {};
    const decisionObj = normalizeDecision(data);

    const volatility = (sig?.volatility ?? "STABLE");
    const category = (sig?.volatility_category ?? "GENERAL");

    const evidenceStatus = (sig?.evidence_validation_status ?? "MISSING");
    const evidenceTrustTier = (sig?.evidence_trust_tier ?? "—");
    const evidenceConfidence = (typeof sig?.evidence_confidence === "number") ? sig.evidence_confidence : null;

    const policyMode = data?.policy?.mode || data?.policy_mode || CONFIG.POLICY_MODE;
    const policyVer  = data?.policy?.version || data?.policy_version || "";
    const policyHash = data?.policy?.hash || data?.policy_hash || "";

    const executionCommit = data?.execution_commit ?? {
      authorized: false,
      action: null,
      event_id: null,
      policy_hash: null,
      audit_fingerprint_sha256: null
    };

    return {
      schema_version: data?.contract?.schema_version || data?.schema_version || "2.0",
      request_id: data?.contract?.request_id || data?.request_id || data?.audit?.event_id || null,

      decision: (decisionObj.action || "REVIEW"),
      readiness_signal: data?.readiness_signal ?? data?.score ?? "--",
      verdict: data?.verdict || "",

      policy_mode: policyMode,
      policy_version: policyVer,
      policy_hash: policyHash,

      audit: {
        event_id: data?.audit?.event_id || data?.event_id || "",
        audit_fingerprint_sha256: data?.audit?.audit_fingerprint_sha256 || data?.audit_fingerprint_sha256 || ""
      },

      latency_ms: (typeof data?.latency_ms === "number") ? data.latency_ms : null,

      volatility: String(volatility).toUpperCase(),
      volatility_category: String(category).toUpperCase(),
      evidence_validation_status: evidenceStatus,
      evidence_trust_tier: evidenceTrustTier,
      evidence_confidence: evidenceConfidence,

      // Safe exposure: risk_flags + guardrail are OK for MVP marketing.
      // (We do NOT expose rules_fired / heuristics internals.)
      risk_flags: Array.isArray(sig?.risk_flags) ? sig.risk_flags : [],
      guardrail: sig?.guardrail ?? null,

      execution_boundary: data?.execution_boundary ?? false,
      execution_commit: executionCommit,

      // Safe, limited extras
      references: Array.isArray(data?.references) ? data.references : [],
      explanation: data?.explanation || ""
    };
  }

  function renderResponse(data) {
    const publicContract = buildPublicContract(data);
    lastResponsePublic = publicContract;

    const readiness = publicContract?.readiness_signal ?? "--";
    setText(scoreDisplay, readiness);
    setText(scoreVerdict, publicContract?.verdict || "");
    updateGauge(readiness);

    setText(volatilityValue, publicContract?.volatility || "STABLE");

    const pMode = publicContract?.policy_mode || CONFIG.POLICY_MODE;
    const pVer  = publicContract?.policy_version || "";
    const pHash = publicContract?.policy_hash || "";
    const policyLabel = pVer ? `${pMode} v${pVer}` + (pHash ? ` (hash: ${pHash})` : "") : `${pMode}`;
    setText(policyValue, policyLabel);

    const decisionObj = normalizeDecision(data);
    const action = (decisionObj.action || "REVIEW").toUpperCase();
    const reason = decisionObj.reason || "";

    if (decisionCard) show(decisionCard, true);
    setText(decisionAction, action);
    applyDecisionColor(action);
    setText(decisionReason, reason);

    const ms = (typeof publicContract?.latency_ms === "number") ? publicContract.latency_ms : "—";
    setText(apiMeta, `runtime gate · server ${ms}ms`);

    // ---- Execution Commit (downstream enforcement artifact) ----
    const exec = publicContract?.execution_commit || null;

    const execCard = document.getElementById("execCommitCard");
    const execBoundary = document.getElementById("execBoundary");
    const execAuthorized = document.getElementById("execAuthorized");
    const execAction = document.getElementById("execAction");
    const execEventId = document.getElementById("execEventId");
    const execPolicyHash = document.getElementById("execPolicyHash");
    const execAudit = document.getElementById("execAudit");

    if (execBoundary) {
      const boundary = publicContract?.execution_boundary === true;
      execBoundary.textContent = boundary ? "TRUE" : "FALSE";
      execBoundary.style.fontWeight = "700";
    }

    if (exec && exec.authorized !== undefined) {
      if (execCard) execCard.style.display = "block";

      const authorized = exec.authorized === true;
      if (execAuthorized) {
        execAuthorized.textContent = authorized ? "YES" : "NO";
        execAuthorized.style.fontWeight = "700";
      }

      if (execAction) execAction.textContent = exec.action || "—";
      if (execEventId) execEventId.textContent = exec.event_id || "—";
      if (execPolicyHash) execPolicyHash.textContent = exec.policy_hash || "—";

      if (execAudit) {
        execAudit.textContent =
          exec.audit_fingerprint_sha256 ||
          publicContract?.audit?.audit_fingerprint_sha256 ||
          "—";
      }
    } else {
      if (execCard) execCard.style.display = "none";
    }

    // ---- Safe output text ----
    // Only show sanitized public contract + short “validation details”.
    const safeDetails = {
      contract: data?.contract || null,
      decision: data?.decision || null,
      policy: data?.policy || null,
      audit: data?.audit || null
    };

    const fullText =
      `Execution Decision Artifact (live) · ${apiMeta?.textContent || ""}\n` +
      `${safeJson(publicContract)}\n\n` +
      `Validation details, explanation & references\n` +
      `${safeJson({
        explanation: publicContract.explanation,
        references: publicContract.references,
        signals: {
          volatility: publicContract.volatility,
          volatility_category: publicContract.volatility_category,
          evidence_validation_status: publicContract.evidence_validation_status,
          evidence_trust_tier: publicContract.evidence_trust_tier,
          evidence_confidence: publicContract.evidence_confidence,
          risk_flags: publicContract.risk_flags,
          guardrail: publicContract.guardrail
        },
        meta: safeDetails
      })}`;

    if (resultPre) resultPre.textContent = fullText;
  }
  async function postToFirstAvailableEndpoint(payload) {
    // Prefer /api/score (your backend), fall back to /api/runtime (older frontend)
    const paths = ["/api/score", "/api/runtime"];

    let lastErrText = "";
    for (const path of paths) {
      const url = `${CONFIG.API_BASE}${path}`;

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
          t = stripHtml(t) || `HTTP ${res.status}`;
          lastErrText = t;

          // If this endpoint doesn't exist, try the next one.
          if (res.status === 404) continue;

          // Otherwise fail immediately (bad request, server error, etc.)
          return { ok: false, status: res.status, text: t, pathTried: path };
        }

        let data = null;
        try {
          data = await res.json();
        } catch (e) {
          const txt = stripHtml(await res.text().catch(() => "")) || String(e);
          return { ok: false, status: 0, text: txt, pathTried: path };
        }

        // Success
        return { ok: true, data, pathUsed: path };
      } catch (e) {
        // Timeout / network errors: do not keep retrying other paths endlessly if it's not 404.
        lastErrText = String(e);
      }
    }

    return { ok: false, status: 404, text: lastErrText || "No supported endpoint found.", pathTried: "/api/score,/api/runtime" };
  }

  async function onVerify() {
    const text = (claimBox?.value || "").trim();
    const evidence = (evidenceBox?.value || "").trim();

    if (!text) return alert("Paste AI- or agent-generated text first.");

    const payload = { text, evidence: evidence || "", policy_mode: CONFIG.POLICY_MODE };
    lastPayload = payload;
    setPendingUI();

    try {
      const out = await postToFirstAvailableEndpoint(payload);

      if (!out.ok) {
        setErrorUI(
          "could not evaluate. Check backend route and try again.",
          out.text || (out.status ? `HTTP ${out.status}` : "Unknown error")
        );
        return;
      }

      // Track which endpoint succeeded for curl generation
      lastEndpointPath = out.pathUsed;

      const data = out.data;

      if (data?.error_code) {
        setErrorUI(data?.message || "Request error.", CONFIG.SHOW_DEBUG ? safeJson(data) : "");
        return;
      }

      renderResponse(data);
    } catch (e) {
      const msg = (String(e || "").includes("AbortError"))
        ? "Request timed out. Backend may be waking up. Try again."
        : "Request failed. Network or backend unavailable.";
      setErrorUI(msg, String(e));
    }
  }

  if (verifyButton) verifyButton.addEventListener("click", onVerify);
  else console.warn("VERIFY button not found. Check id/class.");

  // keep existing hook used by your HTML
  window.scoreText = function () {
    if (verifyButton) verifyButton.click();
    else onVerify();
  };

  // ---- Copy helpers (safe) ----
  window.copyJSONPayload = function () {
    if (!CONFIG.SHOW_COPY_PAYLOAD) return;
    if (!lastPayload) return alert("Run an evaluation first.");
    copyToClipboard(safeJson(lastPayload));
  };

  window.copyResponse = function () {
    if (!CONFIG.SHOW_COPY_RESPONSE) return;
    if (!lastResponsePublic) return alert("Run an evaluation first.");
    copyToClipboard(safeJson(lastResponsePublic));
  };

  window.copyCurl = function () {
    if (!CONFIG.SHOW_COPY_CURL) return;
    if (!lastPayload) return alert("Run an evaluation first.");

    const path = lastEndpointPath || "/api/score";
    const curl = `curl -X POST "${CONFIG.API_BASE}${path}" -H "Content-Type: application/json" -d '${JSON.stringify(lastPayload)}'`;
    copyToClipboard(curl);
  };

  // Initialize
  setPendingUI();

  window.TruCiteDebug = {
    config: CONFIG,
    lastPayload: () => lastPayload,
    lastResponsePublic: () => lastResponsePublic,
    lastEndpointPath: () => lastEndpointPath
  };
})();
