(() => {
  // ---------- Helpers ----------
  const $ = (sel) => document.querySelector(sel);
  const byId = (id) => document.getElementById(id);

  // Try a list of selectors/ids and return first match
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

  // ---------- Element binding (robust) ----------
  const verifyBtn = pick(
    "verifyBtn",
    "verify",
    "#verifyBtn",
    "#verify",
    "button.primary-btn",
    "button[data-action='verify']",
    "button"
  );

  const claimBox = pick(
    "inputText",
    "claimText",
    "claimInput",
    "#inputText",
    "#claimText",
    "textarea[name='claim']",
    "textarea"
  );

  const evidenceBox = pick(
    "evidenceText",
    "evidence",
    "evidenceInput",
    "#evidenceText",
    "#evidence",
    "textarea[name='evidence']",
    "textarea[placeholder*='evidence']"
  );

  const scoreDisplay = pick("scoreDisplay", "score", "#scoreDisplay", "#score");
  const scoreVerdict = pick("scoreVerdict", "verdict", "#scoreVerdict", "#verdict");

  const gaugeFill = pick("gaugeFill", "#gaugeFill", ".gauge-fill");

  const decisionBox = pick(
    "decisionBox",
    "decisionGate",
    "#decisionBox",
    "#decisionGate",
    ".decision-box",
    ".decision-card"
  );

  const decisionAction = pick(
    "decisionAction",
    "decision",
    "#decisionAction",
    "#decision",
    ".decision-action"
  );

  const decisionReason = pick(
    "decisionReason",
    "decisionMsg",
    "#decisionReason",
    "#decisionMsg",
    ".decision-reason"
  );

  // JSON area: could be <pre id="result">, or a div/text area
  const resultPre = pick("result", "jsonOutput", "#result", "#jsonOutput", "pre", ".json-box");

  // Copy buttons
  const copyJsonBtn = pick("copyJson", "copyPayload", "#copyJson", "#copyPayload", "button[data-copy='json']");
  const copyCurlBtn = pick("copyCurl", "copycurl", "#copyCurl", "#copycurl", "button[data-copy='curl']");
  const copyRespBtn = pick("copyResp", "copyResponse", "#copyResp", "#copyResponse", "button[data-copy='resp']");

  // If your page has multiple buttons, we try to lock to the VERIFY button by text
  function ensureVerifyButton(btn) {
    if (!btn) return null;
    const t = (btn.textContent || "").trim().toUpperCase();
    if (t === "VERIFY") return btn;

    // Find a button with text VERIFY
    const allBtns = Array.from(document.querySelectorAll("button"));
    const v = allBtns.find(b => ((b.textContent || "").trim().toUpperCase() === "VERIFY"));
    return v || btn;
  }

  const verifyButton = ensureVerifyButton(verifyBtn);

  // ---------- State ----------
  let lastPayload = null;
  let lastResponse = null;

  // ---------- Styling (inline fallback) ----------
  function applyDecisionColor(action) {
    if (!decisionAction) return;

    const a = (action || "").toUpperCase();

    // Remove known classes if present
    decisionAction.classList.remove("allow", "review", "block");

    // Also apply inline style so it works even if CSS missing
    decisionAction.style.fontWeight = "900";

    if (a === "ALLOW") {
      decisionAction.classList.add("allow");
      decisionAction.style.color = "#28d17c"; // green
    } else if (a === "BLOCK") {
      decisionAction.classList.add("block");
      decisionAction.style.color = "#ff3b3b"; // red
    } else {
      decisionAction.classList.add("review");
      decisionAction.style.color = "#FFD700"; // gold
    }
  }

  function updateGauge(score) {
    if (!gaugeFill) return;
    const s = Math.max(0, Math.min(100, Number(score) || 0));
    const maxDeg = 180;
    const rot = (s / 100) * maxDeg;
    gaugeFill.style.transform = `rotate(${rot}deg)`;
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
  function buildApiUrl() {
    // Prefer relative (works when frontend is served from backend domain)
    // If you ever host frontend elsewhere, it still works by using full origin.
    const rel = "/verify";
    try {
      // If current origin is valid, use it as fallback
      const abs = `${location.origin}${rel}`;
      return { rel, abs };
    } catch {
      return { rel, abs: rel };
    }
  }

  async function callVerify(payload) {
    const { rel, abs } = buildApiUrl();

    // First try relative
    let res = await fetch(rel, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).catch(() => null);

    // If relative failed (CORS / different host), try absolute
    if (!res) {
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

      // Useful diagnostic if route mismatch
      if (!res || !res.ok) {
        let detailText = "";
        try {
          const t = await res.text();
          detailText = t;
        } catch {}
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
  // Start state
  setPendingUI();

  // Expose minimal debug hooks (optional)
  window.TruCiteDebug = {
    elements: { verifyButton, claimBox, evidenceBox, scoreDisplay, scoreVerdict, gaugeFill, decisionBox, decisionAction, decisionReason, resultPre },
    lastPayload: () => lastPayload,
    lastResponse: () => lastResponse
  };
})();
