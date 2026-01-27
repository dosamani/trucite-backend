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
  const verifyButton = pick("verifyBtn", "#verifyBtn", "button.primary-btn");
  const claimBox = pick("inputText", "#inputText", "textarea");
  const evidenceBox = pick("evidenceText", "#evidenceText");
  const scoreDisplay = pick("scoreDisplay", "#scoreDisplay");
  const scoreVerdict = pick("scoreVerdict", "#scoreVerdict");
  const gaugeFill = pick("gaugeFill", "#gaugeFill");
  const decisionBox = pick("decisionBox", "#decisionBox", ".decision-card");
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

  function updateGauge(score) {
    if (!gaugeFill) return;
    const s = Math.max(0, Math.min(100, Number(score) || 0));
    const maxDeg = 180;
    gaugeFill.style.strokeDashoffset = 260 - (s / 100) * 260;
  }

  function setPendingUI() {
    setText(scoreDisplay, "--");
    setText(scoreVerdict, "Score pending...");
    setText(decisionAction, "—");
    setText(decisionReason, "Awaiting verification...");
    updateGauge(0);
    if (resultPre) resultPre.textContent = "";
  }

  function renderResponse(data) {
    lastResponse = data;
    const score = data?.score ?? "--";
    setText(scoreDisplay, score);
    setText(scoreVerdict, data?.verdict || "");
    updateGauge(score);

    const action = data?.decision?.action || "REVIEW";
    const reason = data?.decision?.reason || "";
    setText(decisionAction, action);
    applyDecisionColor(action);
    setText(decisionReason, reason);

    if (resultPre) resultPre.textContent = safeJson(data);
  }

  async function onVerify() {
    const text = claimBox?.value?.trim();
    if (!text) return alert("Paste AI text first.");

    const payload = { text, evidence: evidenceBox?.value || "", policy_mode: "enterprise" };
    lastPayload = payload;
    setPendingUI();

    try {
      const res = await fetch("/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      renderResponse(data);
    } catch (e) {
      setText(scoreVerdict, "Error");
      setText(decisionReason, "Backend error.");
    }
  }

  if (verifyButton) verifyButton.addEventListener("click", onVerify);

  // ---------- COPY FUNCTIONS (NEW) ----------
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

  setPendingUI();
})();
