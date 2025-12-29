document.addEventListener("DOMContentLoaded", function () {
  const form = document.getElementById("claimForm");
  const input = document.getElementById("claimInput");
  const output = document.getElementById("result");

  if (!form || !input || !output) {
    console.error("UI elements not found. Check your index.html IDs.");
    return;
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();

    const claimText = input.value.trim();
    if (!claimText) {
      output.innerText = "Please enter a claim to verify.";
      return;
    }

    output.innerText = "Analyzing claim...";

    // IMPORTANT: relative path, same-origin
    const endpoint = "/verify";

    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: claimText }),
      });

      // If not ok, capture whatever the server returned (HTML or JSON)
      if (!response.ok) {
        const raw = await response.text();
        const snippet = raw ? raw.slice(0, 400) : "";
        throw new Error(
          `POST ${endpoint} failed. HTTP ${response.status}. ` +
            (snippet ? `Body snippet: ${snippet}` : "")
        );
      }

      // Parse JSON safely
      let data;
      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        data = await response.json();
      } else {
        const raw = await response.text();
        throw new Error(
          `Expected JSON but got content-type=${contentType}. Body snippet: ${raw.slice(
            0,
            400
          )}`
        );
      }

      // Display results
      let display = "";
      display += `Verdict: ${data.verdict}\n`;
      display += `Score: ${data.score}\n\n`;
      display += `Event ID: ${data.event_id}\n`;

      if (data.audit_fingerprint && data.audit_fingerprint.timestamp_utc) {
        display += `Timestamp: ${data.audit_fingerprint.timestamp_utc}\n\n`;
      } else {
        display += `Timestamp: (missing)\n\n`;
      }

      if (data.claims && data.claims.length > 0) {
        display += "Claims:\n";
        data.claims.forEach((c, i) => {
          display += `${i + 1}. ${c.text}\n`;
        });
      }

      // Optional: show db logging state if backend returns it
      if (typeof data.db_logged !== "undefined") {
        display += `\nDB Logged: ${data.db_logged}\n`;
      }

      output.innerText = display;
    } catch (err) {
      console.error(err);
      output.innerText =
        "Backend connection failed\n\n" +
        (err && err.message ? err.message : "Unknown error");
    }
  });
});
