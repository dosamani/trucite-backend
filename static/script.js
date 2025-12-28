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

        try {
            const response = await fetch("/verify", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ text: claimText })
            });

            if (!response.ok) {
                throw new Error("Server returned error " + response.status);
            }

            const data = await response.json();

            let display = "";
            display += `Verdict: ${data.verdict}\n`;
            display += `Score: ${data.score}\n\n`;
            display += `Event ID: ${data.event_id}\n`;
            display += `Timestamp: ${data.audit_fingerprint.timestamp_utc}\n\n`;

            if (data.claims && data.claims.length > 0) {
                display += "Claims:\n";
                data.claims.forEach((c, i) => {
                    display += `${i + 1}. ${c.text}\n`;
                });
            }

            output.innerText = display;

        } catch (err) {
            console.error(err);
            output.innerText = "Error communicating with TruCite engine.";
        }
    });

});
