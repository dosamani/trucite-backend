// /static/script.js
// TruCite MVP - frontend verify handler (explicit backend URL to avoid 404s from embeds/previews)

const API_BASE = "https://trucite-backend.onrender.com"; // <-- your Render backend service

async function scoreText() {
  const input = document.getElementById("inputText");
  const result = document.getElementById("result");
  const scoreDisplay = document.getElementById("scoreDisplay");
  const scoreVerdict = document.getElementById("scoreVerdict");
  const gaugeFill = document.getElementById("gaugeFill");

  // Hard fail fast if IDs mismatch
  if (!input || !result || !scoreDisplay || !scoreVerdict || !gaugeFill) {
    console.error("Missing required elements. Check IDs in index.html.");
    return;
  }

  const text = (input.value || "").trim();

  if (!text) {
    result.textContent = "Please paste AI- or agent-generated text to verify.";
    scoreDisplay.textContent = "--";
    scoreVerdict.textContent = "Score pending…";
    gaugeFill.style.strokeDashoffset = "260";
    return;
  }

  // UI reset
  result.textContent = "Analyzing…";
  scoreDisplay.textContent = "--";
  scoreVerdict.textContent = "Score pending…";
  gaugeFill.style.strokeDashoffset = "260";

  try {
    // IMPORTANT: Call backend explicitly so /verify never 404s due to origin mismatch
    const resp = await fetch(`${API_BASE}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });

    // If backend returns HTML (common for 404/502), show it clearly
    const contentType = resp.headers.get("content-type") || "";
    if (!resp.ok) {
      const bodyText = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${bodyText}`);
    }

    const data = contentType.includes("application/json")
      ? await resp.json()
      : JSON.parse(await resp.text());

    // Pull top-level score/verdict (fallback to first-claim if needed)
    const score = Number(
      data?.score ?? data?.claims?.[0]?.score ?? 0
    );

    const verdict =
      data?.verdict ?? data?.claims?.[0]?.verdict ?? "--";

    scoreDisplay.textContent = String(score);
    scoreVerdict.textContent = verdict;

    // Gauge fill (0..100 maps to 260..0 dashoffset)
    const clamped = Math.max(0, Math.min(100, score));
    const offset = 260 - (260 * (clamped / 100));
    gaugeFill.style.strokeDashoffset = String(offset);

    // Render full response for now (MVP)
    result.textContent = JSON.stringify(data, null, 2);

  } catch (e) {
    console.error(e);
    scoreVerdict.textContent = "Error";
    scoreDisplay.textContent = "--";
    gaugeFill.style.strokeDashoffset = "260";
    result.textContent = `Error communicating with TruCite engine:\n${e.message}`;
  }
}
```0
