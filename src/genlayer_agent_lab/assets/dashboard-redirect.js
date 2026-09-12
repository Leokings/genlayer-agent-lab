"use strict";
// Preserve the local setup sign-in handoff; the unified dashboard consumes it.
const handoff = new URLSearchParams(location.hash.slice(1));
handoff.set("mode", "glsim");
location.replace("/assets/workflows.html#" + handoff);
