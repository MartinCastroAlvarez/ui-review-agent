// Demo site script. Two deliberate bugs and two requests the review sandbox must block.
"use strict";

// BUG failed-request: the stats endpoint does not exist (404).
fetch("api/stats.json").then((r) => r.ok && r.json()).catch(() => {});

// Analytics beacon: a POST, which the sandbox never lets through.
navigator.sendBeacon("collect", JSON.stringify({ page: location.pathname }));

// A real form submission attempt on load: cancelled by the sandbox.
const form = document.querySelector("form");
if (form) form.requestSubmit();

// BUG page-error: reads a property of a missing element.
document.querySelector("#promo-banner").classList.add("visible");
