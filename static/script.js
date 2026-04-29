(() => {
  const CONFIG = {
    API_BASE: "https://trucite-backend.onrender.com",
    POLICY_MODE: "enterprise",
    TIMEOUT_MS: 15000,
    SHOW_DEBUG: false,
    SHOW_COPY_PAYLOAD: true,
    SHOW_COPY_CURL: true,
    SHOW_COPY_RESPONSE: true
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

  function setText(el, txt) {
    if (el) el.textContent = txt;
  }

  function show(el, on = true) {
    if (el) el.style.display = on ? "" : "none";
  }

  function safeJson(obj) {
    try {
      return JSON.stringify(obj, null, 2);
    } catch {
      return String(obj);
    }
  }

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
    } else {
      fallbackCopy(text);
    }
  }

  function fallbackCopy(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.top = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
    } catch {}
    document.body.removeChild(ta);
  }

  function stripHtml(raw) {
    if (!raw) return "";
    const s = String(raw);
    if (s.includes("<!doctype") || s.includes("<html")) {
      return "[backend returned HTML error body]";
    }
    return s;
  }

  let verifyButton = pick("verifyBtn", "#verifyBtn", "button.primary-btn");
  if (!verifyButton) {
    const allBtns = Array.from(document.querySelectorAll("button"));
    verifyButton =
      allBtns.find((b) => ["VERIFY", "EVALUATE"].includes((b.textContent || "").trim().toUpperCase())) || null;
  }

  const claimBox = pick("inputText", "#inputText", "textarea");
  const evidenceBox = pick("evidenceText", "#evidenceText");

  const scoreDisplay = pick("scoreDisplay", "#scoreDisplay");
  const scoreVerdict = pick("scoreVerdict", "#scoreVerdict");
  const gaugeFill = pick("gaugeFill", "#gaugeFill");

  const decisionCard = pick(".decision-card", "#decisionBox");
  const decisionAction = pick("decisionAction", "#decisionAction");
  const decisionReason = pick("decisionReason", "#decisionReason");

  const resultPre = pick("result", "#result");

  const volatilityValue = pick("volatilityValue", "#volatilityValue");
  const policyValue = pick("policyValue", "#policyValue");
  const apiMeta = pick("apiMeta", "#apiMeta");

  let lastPayload = null;
  let lastResponsePublic = null;
  let lastEndpointPath = null;

  function applyDecisionColor(action) {
    if (!decisionAction) return;
    decisionAction.classList.remove("allow", "review", "block", "error");

    const a = (action || "").toUpperCase();
    if (a === "ALLOW") decisionAction.classList.add("allow");
    else if (a === "BLOCK") decisionAction.classList.add("block");
    else if (a === "ERROR") decisionAction.classList.add("error");
    else decisionAction.classList.add("review");
  }

  function applyDecisionCardColor(action) {
    if (!decisionCard) return;
    decisionCard.classList.remove("allow", "review", "block", "error");

    const a = (action || "").toUpperCase();
    if (a === "ALLOW") decisionCard.classList.add("allow");
    else if (a === "BLOCK") decisionCard.classList.add("block");
    else if (a === "ERROR") decisionCard.classList.add("error");
    else decisionCard.classList.add("review");
  }

  function updateGauge(val) {
    if (!gaugeFill) return;
    const s = Math.max(0, Math.min(100, Number(val) || 0));
    const total = 260;

    gaugeFill.style.transition = "none";
    gaugeFill.style.strokeDashoffset = total;

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        gaugeFill.style.transition = "stroke-dashoffset 0.9s ease";
        gaugeFill.style.strokeDashoffset = total - (s / 100) * total;
      });
    });
  }

  function setPendingUI() {
    setText(scoreDisplay, "--");
    setText(scoreVerdict, "Signal pending...");
    show(decisionCard, true);
    setText(decisionAction, "—");
    setText(decisionReason, "Awaiting evaluation...");
    applyDecisionColor("REVIEW");
    applyDecisionCardColor("REVIEW");
    updateGauge(0);

    if (resultPre) resultPre.textContent = "";
    setText(volatilityValue, "—");
    setText(policyValue, "—");
    setText(apiMeta, "runtime gate · server —ms");

    const execCard = byId("execCommitCard");
    if (execCard) execCard.style.display = "none";
  }

  function setErrorUI(msg, debug) {
    setText(scoreDisplay, "--");
    setText(scoreVerdict, "System error");
    show(decisionCard, true);
    setText(decisionAction, "ERROR");
    applyDecisionColor("ERROR");
    applyDecisionCardColor("ERROR");
    setText(decisionReason, msg);

    if (resultPre) {
      resultPre.textContent = CONFIG.SHOW_DEBUG ? debug : msg;
    }

    const execCard = byId("execCommitCard");
    if (execCard) execCard.style.display = "none";
  }

  function resetForm() {
    if (claimBox) claimBox.value = "";
    if (evidenceBox) evidenceBox.value = "";
    if (resultPre) resultPre.textContent = "";

    setText(scoreDisplay, "--");
    setText(scoreVerdict, "Signal pending...");
    setText(decisionAction, "—");
    setText(decisionReason, "Awaiting evaluation...");
    applyDecisionColor("REVIEW");
    applyDecisionCardColor("REVIEW");
    updateGauge(0);

    setText(volatilityValue, "—");
    setText(policyValue, "—");
    setText(apiMeta, "runtime gate · server —ms");

    const execCard = byId("execCommitCard");
    if (execCard) execCard.style.display = "none";

    lastPayload = null;
    lastResponsePublic = null;
    lastEndpointPath = null;
  }

  function normalizeDecision(data) {
    const raw = data?.decision;

    if (typeof raw === "object") {
      return {
        action: raw.action || "REVIEW",
        reason: raw.reason || ""
      };
    }

    return {
      action: raw || "REVIEW",
      reason: data?.reason || ""
    };
  }

  function buildPublicContract(data) {
    const sig = data?.signals || {};
    const decision = normalizeDecision(data);

    return {
      decision: decision.action,
      readiness_signal: data?.readiness_signal ?? data?.score ?? "--",
      verdict: data?.verdict || "",
      policy_mode: data?.policy?.mode || CONFIG.POLICY_MODE,
      latency_ms: data?.latency_ms ?? null,
      volatility: sig?.volatility || "STABLE",
      evidence_validation_status: sig?.evidence_validation_status || "MISSING",
      risk_flags: sig?.risk_flags || [],
      references: data?.references || [],
      explanation: data?.explanation || ""
    };
  }

  function renderExecutionCommit(contract) {
    const execCard = byId("execCommitCard");
    if (!execCard) return;

    execCard.style.display = "block";

    const execText = byId("execCommitText");
    if (!execText) return;

    const decision = (contract?.decision || "REVIEW").toUpperCase();

    let msg = "";
    if (decision === "ALLOW") {
      msg = "Execution authorized. Downstream systems may proceed.";
    } else if (decision === "BLOCK") {
      msg = "Execution blocked. Output failed policy validation.";
    } else {
      msg = "Execution requires review before proceeding.";
    }

    execText.textContent = msg;
  }

  function renderResponse(data) {
    const contract = buildPublicContract(data);
    lastResponsePublic = contract;

    setText(scoreDisplay, contract.readiness_signal);
    setText(scoreVerdict, contract.verdict);
    updateGauge(contract.readiness_signal);

    setText(volatilityValue, contract?.volatility || "—");

    const pMode = contract?.policy_mode || CONFIG.POLICY_MODE;
    setText(policyValue, pMode);

    const ms = contract?.latency_ms ?? "—";
    setText(apiMeta, `runtime gate · server ${ms}ms`);

    const decision = normalizeDecision(data);

    setText(decisionAction, decision.action);
    applyDecisionColor(decision.action);
    applyDecisionCardColor(decision.action);

    let reason = decision.reason || "";

    if (!reason && decision.action === "ALLOW") {
      reason = "Evidence and policy checks passed for current execution path.";
    } else if (!reason && decision.action === "BLOCK") {
      reason = "Execution blocked under current policy.";
    } else if (!reason) {
      reason = "Needs verification before downstream execution.";
    }

    setText(decisionReason, reason);

    const fullText =
      `Execution Decision Artifact\n` +
      `${safeJson(contract)}\n\n` +
      `Explanation & References\n` +
      `${safeJson({
        explanation: contract.explanation,
        references: contract.references,
        signals: {
          volatility: contract.volatility,
          evidence_validation_status: contract.evidence_validation_status,
          risk_flags: contract.risk_flags
        }
      })}`;

    if (resultPre) {
      resultPre.textContent = fullText;
    }

    renderExecutionCommit(contract);
  }

  async function postToEndpoint(payload) {
    const paths = ["/api/validate", "/api/score", "/api/runtime"];

    for (const path of paths) {
      try {
        const res = await fetchWithTimeout(
          `${CONFIG.API_BASE}${path}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
          },
          CONFIG.TIMEOUT_MS
        );

        if (res.status === 404) continue;

        if (!res.ok) {
          const txt = stripHtml(await res.text());
          return { ok: false, error: txt };
        }

        const data = await res.json();
        lastEndpointPath = path;
        return { ok: true, data };
      } catch (e) {
        continue;
      }
    }

    return { ok: false, error: "No working endpoint found." };
  }

  async function onVerify() {
    const text = (claimBox?.value || "").trim();
    const evidence = (evidenceBox?.value || "").trim();

    if (!text) {
      alert("Paste text first.");
      return;
    }

    const payload = {
      text,
      evidence,
      policy_mode: CONFIG.POLICY_MODE
    };

    lastPayload = payload;
    setPendingUI();

    const res = await postToEndpoint(payload);

    if (!res.ok) {
      setErrorUI("Backend unavailable or route mismatch.", res.error);
      return;
    }

    renderResponse(res.data);
  }

  if (verifyButton) verifyButton.addEventListener("click", onVerify);

  window.scoreText = () => onVerify();
  window.resetForm = resetForm;

  window.copyJSONPayload = () => {
    if (!lastPayload) return alert("Run first.");
    copyToClipboard(safeJson(lastPayload));
  };

  window.copyResponse = () => {
    if (!lastResponsePublic) return alert("Run first.");
    copyToClipboard(safeJson(lastResponsePublic));
  };

  window.copyCurl = () => {
    if (!lastPayload) return alert("Run first.");
    const path = lastEndpointPath || "/api/validate";
    copyToClipboard(
      `curl -X POST "${CONFIG.API_BASE}${path}" -H "Content-Type: application/json" -d '${JSON.stringify(lastPayload)}'`
    );
  };

  if (claimBox) {
    claimBox.addEventListener("paste", () => {
      setTimeout(() => {
        const text = (claimBox.value || "").trim();
        if (text && verifyButton) {
          verifyButton.textContent = "RUN DEMO";
        }
      }, 50);
    });
  }

  setPendingUI();
})();
