// TruCite Frontend Script (compat mode)
// - Works with either:
//   A) form#claimForm + input/textarea#claimInput + pre#result
//   B) textarea#inputText + button onclick="scoreText()" + pre#result

const BACKEND_VERIFY_ENDPOINT = "/verify";

// -------------------------------
// Helper: render output
// -------------------------------
function renderResult(data) {
  const output = document.getElementById("result");
  if (!output) return;

  let display = "";
  display += `Verdict: ${data.verdict ?? "(missing)"}\n`;
  display += `Score: ${data.score ?? "(missing)"}\n\n`;
  display += `Event ID: ${data.event_id ?? "(missing)"}\n`;

  const ts = data?.audit_fingerprint?.timestamp_utc;
  display += `Timestamp: ${ts ?? "(missing)"}\n\n`;

  if (Array.isArray(data.claims) && data.claims.length > 0) {
    display += "Claims:\n";
    data.claims.forEach((c, i) => {
      display += `${i + 1}. ${c.text}\n`;
    });
  }

  output.innerText = display;
}

// -------------------------------
// Core POST call
// -------------------------------
async function postVerify(text) {
  const output = document.getElementById("result");
  if (output) output.innerText = "Analyzing claim...";

  const res = await fetch(BACKEND_VERIFY_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text })
  });

  if (!res.ok) {
    const raw = await res.text().catch(() => "");
    throw new Error(`POST ${BACKEND_VERIFY_ENDPOINT} failed. HTTP ${res.status}. ${raw.slice(0, 300)}`);
  }

  const ct = res.headers.get("content-type") || "";
  if (!ct.includes("application/json")) {
    const raw = await res.text().catch(() => "");
    throw new Error(`Expected JSON but got ${ct}. Body: ${raw.slice(0, 300)}`);
  }

  return await res.json();
}

// -------------------------------
// Setup on load
// -------------------------------
document.addEventListener("DOMContentLoaded", function () {
  // OPTION A: Form-based UI
  const form = document.getElementById("claimForm");
  const inputA = document.getElementById("claimInput");

  if (form && inputA) {
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      const claimText = (inputA.value || "").trim();
      const output = document.getElementById("result");

      if (!claimText) {
        if (output) output.innerText = "Please enter a claim to verify.";
        return;
      }

      try {
        const data = await postVerify(claimText);
        renderResult(data);
      } catch (err) {
        console.error(err);
        if (output) output.innerText = "Backend connection failed\n\n" + (err?.message || err);
      }
    });
  }

  // OPTION B: Textarea/button UI (the one you originally used)
  // This does not require any form.
  // If your HTML uses onclick="scoreText()", we define it globally below.
});

// Global function for HTML: onclick="scoreText()"
async function scoreText() {
  const inputB = document.getElementById("inputText");
  const output = document.getElementById("result");

  const claimText = ((inputB && inputB.value) ? inputB.value : "").trim();

  if (!claimText) {
    if (output) output.innerText = "Paste some AI output first, then tap VERIFY.";
    return;
  }

  try {
    const data = await postVerify(claimText);
    renderResult(data);
  } catch (err) {
    console.error(err);
    if (output) output.innerText = "Backend connection failed\n\n" + (err?.message || err);
  }
}
