const API_URL = "/verify";

const verifyBtn = document.getElementById("verifyBtn");
const inputText = document.getElementById("inputText");
const evidenceText = document.getElementById("evidenceText");

const scoreDisplay = document.getElementById("scoreDisplay");
const scoreVerdict = document.getElementById("scoreVerdict");

const decisionBox = document.getElementById("decisionBox");
const decisionAction = document.getElementById("decisionAction");
const decisionReason = document.getElementById("decisionReason");

const resultPre = document.getElementById("result");

const copyJsonBtn = document.getElementById("copyJson");
const copyCurlBtn = document.getElementById("copyCurl");
const copyRespBtn = document.getElementById("copyResp");

let lastPayload = null;
let lastResponse = null;

verifyBtn.addEventListener("click", async () => {
  const text = inputText.value.trim();
  const evidence = evidenceText.value.trim();

  if (!text) {
    alert("Please paste AI-generated text to verify.");
    return;
  }

  const payload = {
    text: text,
    evidence: evidence || "",
    policy_mode: "enterprise"
  };

  lastPayload = payload;

  scoreDisplay.textContent = "--";
  scoreVerdict.textContent = "Scoring...";
  decisionBox.style.display = "none";
  resultPre.textContent = "";

  try {
    const res = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!res.ok) throw new Error("Backend error");

    const data = await res.json();
    lastResponse = data;

    updateGauge(data.score);
    scoreDisplay.textContent = data.score;
    scoreVerdict.textContent = data.verdict;

    showDecision(data.decision);

    resultPre.textContent = JSON.stringify(data, null, 2);

  } catch (err) {
    scoreDisplay.textContent = "Error";
    scoreVerdict.textContent = "Backend unavailable";
    decisionBox.style.display = "block";
    decisionAction.textContent = "REVIEW";
    decisionReason.textContent = "Backend unavailable or route mismatch.";
    decisionAction.className = "review";
  }
});

function updateGauge(score) {
  const gaugeFill = document.getElementById("gaugeFill");
  const maxDegrees = 180;
  const rotation = (score / 100) * maxDegrees;
  gaugeFill.style.transform = `rotate(${rotation}deg)`;
}

function showDecision(decision) {
  decisionBox.style.display = "block";
  decisionAction.textContent = decision.action;
  decisionReason.textContent = decision.reason;

  decisionAction.classList.remove("allow", "review", "block");

  const action = (decision.action || "").toUpperCase();
  if (action === "ALLOW") decisionAction.classList.add("allow");
  if (action === "REVIEW") decisionAction.classList.add("review");
  if (action === "BLOCK") decisionAction.classList.add("block");
}

/* ================= COPY BUTTONS ================= */

copyJsonBtn.addEventListener("click", () => {
  if (!lastPayload) return;
  navigator.clipboard.writeText(JSON.stringify(lastPayload, null, 2));
});

copyCurlBtn.addEventListener("click", () => {
  if (!lastPayload) return;
  const curl = `curl -X POST ${location.origin}/verify -H "Content-Type: application/json" -d '${JSON.stringify(lastPayload)}'`;
  navigator.clipboard.writeText(curl);
});

copyRespBtn.addEventListener("click", () => {
  if (!lastResponse) return;
  navigator.clipboard.writeText(JSON.stringify(lastResponse, null, 2));
});
