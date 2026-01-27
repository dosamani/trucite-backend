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

  function setText(el, txt) {
    if (!el) return;
    el.textContent = txt;
  }

  function show(el, on = true) {
    if (!el) return;
    el.style.display = on ? "" : "none";
  }

  function safeJson(obj) {
    try { return JSON.stringify(obj, null, 2); }
    catch { return String(obj); }
  }

  function copyToClipboard(text) {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
    } else {
      fallbackCopy(text);
    }
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
  const verifyBtn = pick("verifyBtn", "verify", "#verifyBtn", "#verify", "button.primary-btn", "button");
  const claimBox = pick("inputText", "#inputText", "textarea");
  const evidenceBox = pick("evidenceText", "#evidenceText", "textarea[placeholder*='evidence']");

  const scoreDisplay = pick("scoreDisplay", "#scoreDisplay");
  const scoreVerdict = pick("scoreVerdict", "#scoreVerdict");
  const gaugeFill = pick("gaugeFill", "#gaugeFill");

  const decisionBox = pick("decisionBox", "#decisionBox", ".decision-box", ".decision-card");
  const decisionAction = pick("decisionAction", "#decisionAction", ".decision-action");
  const decisionReason = pick("decisionReason", "#decisionReason", ".decision-reason");

  const resultPre = pick("result", "#result", "pre");

  const copyJsonBtn = pick("copyPayload", "#copyPayload");
  const copyCurlBtn = pick("copyCurl", "#copyCurl");
  const copyRespBtn = pick("copyResponse", "#copyResponse");

  function ensureVerifyButton(btn) {
    if (!btn) return null;
    const t = (btn.textContent || "").trim().toUpperCase();
    if (t === "VERIFY") return btn;
    const allBtns = Array.from(document.querySelectorAll("button"));
    const v = allBtns.find(b => ((b.textContent || "").trim().toUpperCase() === "VERIFY"));
    return v || btn;
  }

  const verifyButton = ensureVerifyButton(verifyBtn);

  // ---------- State ----------
  let lastPayload = null;
  let lastResponse = null;

  // ---------- Styling ----------
  function applyDecisionColor(action) {
    if (!decisionAction) return;

    const a = (action || "").toUpperCase();
    decisionAction.classList.remove("allow", "review", "block");
    decisionAction.style.fontWeight = "900";

    if (a === "ALLOW") {
      decisionAction.classList.add("allow");
      decisionAction.style.color = "#28d17c";
    } else if (a === "BLOCK") {
      decisionAction.classList.add("block");
      decisionAction.style.color = "#ff3b3b";
    } else {
      decisionAction.classList.add("review");
      decisionAction.style.color = "#FFD700";
    }
  }

  function updateGauge(score) {
    if (!gaugeFill) return;
    const s = Math.max(0, Math.min(100, Number(score) || 0));
    const dash = 260;
    const offset = dash - (dash * (s / 100));
    gaugeFill.style.strokeDasharray = String(dash);
    gaugeFill.style.strokeDashoffset = String(offset);
  }

  function setPendingUI() {
    setText(scoreDisplay, "--");
    setText(scoreVerdict, "Score pending...");
    if (decisionBox) show(decisionBox, true);
    setText(decisionAction, "—");
    if (decisionReason) setText(decisionReason, "Awaiting verification...");
    applyDecisionColor("REVIEW");
    if (resultPre) resultPre.textContent = "";
    updateGauge(0);
  }

  function setErrorUI(msg, details) {
    setText(scoreDisplay, "--");
    setText(scoreVerdict, "Error");
    if (decisionBox) show(decisionBox, true);
    setText(decisionAction, "REVIEW");
    applyDecisionColor("REVIEW");
    if (decisionReason) setText(decisionReason, msg || "Backend unavailable or route mismatch.");
    if (resultPre) {
      const body = details ? `Backend error: ${details}` : (msg || "Backend error");
      resultPre.textContent = body;
    }
  }

  function renderResponse(data) {
    lastResponse = data;

    const score = data?.score ?? "--";
    setText(scoreDisplay, score);
    setText(scoreVerdict, data?.verdict || "");
    updateGauge(Number(score) || 0);

    const action = data?.decision?.action || "REVIEW";
    const reason = data?.decision?.reason || "";

    if (decisionBox) show(decisionBox, true);
    setText(decisionAction, action);
    applyDecisionColor(action);
    setText(decisionReason, reason);

    if (resultPre) resultPre.textContent = safeJson(data);
  }

  // ---------- API ----------
  async function callVerify(payload) {
    const rel = "/verify";
    let res = await fetch(rel, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).catch(() => null);

    if (!res) {
      const abs = `${location.origin}${rel}`;
      res = await fetch(abs, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    }
    return res;
  }

  // ---------- Main click handler ----------
  async function onVerify() {
    const text = (claimBox?.value || "").trim();
    const evidence = (evidenceBox?.value || "").trim();

    if (!text) {
      alert("Paste AI- or agent-generated text first.");
      return;
    }

    const payload = {
      text,
      evidence: evidence || "",
      policy_mode: "enterprise"
    };

    lastPayload = payload;
    setPendingUI();

    try {
      const res = await callVerify(payload);

      if (!res || !res.ok) {
        let detailText = "";
        try { detailText = await res.text(); } catch {}
        setErrorUI("could not score. Check backend route and try again.", detailText || (res ? `${res.status}` : ""));
        return;
      }

      const data = await res.json();
      renderResponse(data);

    } catch (e) {
      setErrorUI("could not score. Check backend route and try again.", String(e));
    }
  }

  // ---------- Copy buttons ----------
  function wireCopyButtons() {
    if (copyJsonBtn) {
      copyJsonBtn.addEventListener("click", () => {
        if (!lastPayload) return;
        copyToClipboard(safeJson(lastPayload));
      });
    }

    if (copyRespBtn) {
      copyRespBtn.addEventListener("click", () => {
        if (!lastResponse) return;
        copyToClipboard(safeJson(lastResponse));
      });
    }

    if (copyCurlBtn) {
      copyCurlBtn.addEventListener("click", () => {
        if (!lastPayload) return;
        const url = `${location.origin}/verify`;
        const curl = `curl -X POST "${url}" -H "Content-Type: application/json" -d '${JSON.stringify(lastPayload)}'`;
        copyToClipboard(curl);
      });
    }
  }

  // ---------- Init ----------
  if (!verifyButton) {
    console.warn("VERIFY button not found. Check your button id/class.");
  } else {
    verifyButton.addEventListener("click", onVerify);
  }

  wireCopyButtons();
  setPendingUI();

  window.TruCiteDebug = {
    elements: { verifyButton, claimBox, evidenceBox, scoreDisplay, scoreVerdict, gaugeFill, decisionBox, decisionAction, decisionReason, resultPre },
    lastPayload: () => lastPayload,
    lastResponse: () => lastResponse
  };
})();
