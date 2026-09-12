"use strict";
(() => {
/** Lossless v2 project JSON. BigInt is encoded explicitly; strings stay strings. */
function encodeProjectWire(value     )      {
  let remaining = 100000;
  function visit(item     , depth        , parents          )      {
    if (--remaining < 0 || depth > 48) throw new Error("Project JSON exceeds its structural limit");
    if (typeof item === "bigint") {
      if (item.toString().replace("-", "").length > 256) throw new Error("Project integer exceeds its digit limit");
      return item >= -9007199254740991n && item <= 9007199254740991n ? Number(item) : {$lab_integer: item.toString()};
    }
    if (typeof item === "number") {
      if (!Number.isFinite(item) || (Number.isInteger(item) && !Number.isSafeInteger(item))) throw new Error("Use BigInt or an exact decimal string for large project integers");
      return item;
    }
    if (item === null || typeof item === "string" || typeof item === "boolean") return item;
    if (typeof item !== "object" || parents.has(item)) throw new Error("Project JSON must be acyclic data");
    const next = new Set(parents); next.add(item);
    if (Array.isArray(item)) return item.map(child => visit(child, depth + 1, next));
    const entries = Object.entries(item).map(([key, child]) => [key, visit(child, depth + 1, next)]);
    const result = Object.fromEntries(entries);
    return entries.length === 1 && ["$lab_integer", "$lab_object"].includes(entries[0][0]          ) ? {$lab_object: result} : result;
  }
  return visit(value, 0, new Set());
}

function decodeProjectWire(value     )      {
  let remaining = 100000;
  function visit(item     , depth        )      {
    if (--remaining < 0 || depth > 48) throw new Error("Project JSON exceeds its structural limit");
    if (item === null || typeof item !== "object") return item;
    if (Array.isArray(item)) return item.map(child => visit(child, depth + 1));
    const keys = Object.keys(item);
    if (keys.length === 1 && keys[0] === "$lab_integer") {
      const text = item.$lab_integer;
      if (typeof text !== "string" || text.replace("-", "").length > 256 || !/^(0|-[1-9][0-9]*|[1-9][0-9]*)$/.test(text)) throw new Error("Invalid exact integer tag");
      const number = BigInt(text);
      return number >= -9007199254740991n && number <= 9007199254740991n ? Number(number) : number;
    }
    if (keys.length === 1 && keys[0] === "$lab_object") {
      const original = item.$lab_object;
      if (!original || Array.isArray(original) || typeof original !== "object" || Object.keys(original).length !== 1 || !["$lab_integer", "$lab_object"].includes(Object.keys(original)[0])) throw new Error("Invalid escaped project object");
      return Object.fromEntries(Object.entries(original).map(([key, child]) => [key, visit(child, depth + 2)]));
    }
    return Object.fromEntries(Object.entries(item).map(([key, child]) => [key, visit(child, depth + 1)]));
  }
  return visit(value, 0);
}

function parseProjectJson(text        )      {
  // Node 24 and current browsers expose the original token to the reviver.
  const parsed = JSON.parse(text, (_key        , value     , context     ) => {
    if (typeof value === "number" && Number.isInteger(value) && !Number.isSafeInteger(value)) {
      if (!context?.source || !/^-?(0|[1-9][0-9]*)$/.test(context.source) || context.source.replace("-", "").length > 256) throw new Error("Cannot read this large integer exactly; use the tagged project format");
      return BigInt(context.source);
    }
    return value;
  });
  return decodeProjectWire(parsed);
}

function stringifyProjectJson(value     , spaceOrReplacer      , space         )         {
  return JSON.stringify(encodeProjectWire(value), null, typeof spaceOrReplacer === "number" ? spaceOrReplacer : space);
}


  const $ = id => document.getElementById(id);
  const key = "genlayer-agent-lab-admin-token";
  // Consume the local setup handoff before any request or external navigation.
  const fragment = new URLSearchParams(location.hash.slice(1));
  let token = fragment.get("token") || "";
  let pairing = null, pairingTimer = null, pairingRevision = 0;
  if (fragment.has("token")) history.replaceState(null, "", location.pathname + location.search);
  try { token ||= sessionStorage.getItem(key) || ""; } catch { /* Private browser sessions may disable storage. */ }
  let selected = null, report = null, templates = [], chosen = null, preview = null;
  let quickTests = null, selectedQuick = null, testMode = fragment.get("mode") === "glsim" ? "glsim" : "studio";
  let workflowHistory = [], scenarioHistory = [];
  let environment = null, refreshing = false, creating = false, revision = 0, detailRevision = 0;
  let preparing = false, readingSpec = false, specReadSequence = 0;
  let environmentCheck = null;
  let connectionRun = null, connectionRevision = 0;
  let renderedReport = null;
  const credentials = new Map(), names = new Map(); // Run secrets live only in this page's memory.
  const timings = new Map();
  const terminal = new Set(["completed", "cancelled", "interrupted", "inconclusive", "failed"]);
  const pretty = value => stringifyProjectJson(value ?? null, 2);
  const text = value => typeof value === "string" ? value : typeof value === "bigint" ? String(value) : pretty(value);
  const words = value => String(value ?? "").replaceAll("_", " ");
  function node(tag, value = "", className = "") {
    const element = document.createElement(tag); element.textContent = value;
    if (className) element.className = className;
    return element;
  }
  function fail(error, target = "notice") {
    if (error?.message === "Workspace changed") return;
    let message = String(error?.message || error);
    for (const secret of [token, ...Array.from(credentials.values(), value => value.token)]) {
      if (secret) message = message.replaceAll(secret, "[redacted]");
    }
    const inline = target === "import-error" && !$("workspace").hidden;
    const alert = $(inline ? "import-error" : "notice");
    if (inline && error instanceof SyntaxError) message = "This is not valid JSON. Check the file or ask your authoring agent to correct it. " + message;
    alert.textContent = message; alert.hidden = false;
    alert.dataset.transient = String(error?.connectionFailure === true);
    if (inline) {
      $("custom-project").open = true;
      $("spec").setAttribute("aria-invalid", "true");
      alert.focus({preventScroll:true});
      alert.scrollIntoView({block:"nearest"});
    }
  }
  function clearImportError() {
    $("import-error").hidden = true; $("import-error").textContent = "";
    $("spec").removeAttribute("aria-invalid");
  }
  function importSpec(value) {
    if (!value.trim()) throw new Error("Choose a project scenario JSON file, or paste its complete contents.");
    const spec = parseProjectJson(value);
    if (!spec || typeof spec !== "object" || Array.isArray(spec)) throw new Error("Use the complete project scenario JSON object from your authoring agent.");
    if (spec.integer_encoding === "lab-tagged-decimal-v1") delete spec.integer_encoding;
    return spec;
  }
  function feedback(message) { $("feedback").textContent = message; $("feedback").hidden = false; }
  function clearNotice() { $("notice").hidden = true; $("feedback").hidden = true; }
  function clearConnectionNotice() { if ($("notice").dataset.transient === "true") $("notice").hidden = true; }
  async function request(path, method = "GET", body, timeoutMs = 30000) {
    const authorization = token;
    let response;
    try {
      response = await fetch(path, {method, redirect:"error", cache:"no-store", signal:AbortSignal.timeout(timeoutMs),
        headers:{Authorization:`Bearer ${authorization}`, ...(body === undefined ? {} : {"Content-Type":"application/json"})},
        body:body === undefined ? undefined : stringifyProjectJson(body)});
    } catch { const error = new Error("The Lab did not respond. Check the setup terminal and your SSH tunnel, then try again."); error.uncertain = true; error.connectionFailure = true; throw error; }
    if (authorization !== token) throw new Error("Workspace changed");
    let result;
    try { result = parseProjectJson(await response.text()); }
    catch { const error = new Error(`The Lab returned an unreadable response (${response.status}). Check its diagnostics.`); error.uncertain = response.ok; throw error; }
    if (!response.ok) {
      if (response.status === 401) {
        token = ""; try { sessionStorage.removeItem(key); } catch {}
        quickTests?.clear(); credentials.clear(); clearSetupPrompt(); $("credential").value = "";
        $("auth").hidden = false; $("workspace").hidden = true; $("disconnect").hidden = true;
      }
      const error = new Error(response.status === 401 ? "Please sign in again. Choose Connect this browser, or use an existing workspace key under Advanced."
        : typeof result.detail === "string" ? result.detail : `The request could not be validated (${response.status}). Check the entered fields.`);
      error.uncertain = response.status >= 500; throw error;
    }
    return result;
  }
  function step(name) {
    for (const item of ["choose", "review", "connect", "results"]) $("step-" + item).classList.toggle("current", item === name);
  }
  function invalidate() {
    clearImportError();
    revision++; preview = null; $("review-confirmed").checked = false; $("review-panel").hidden = true;
    if (!creating) step("choose");
    updateCreate();
  }
  function updateCreate() { $("create").disabled = creating || !preview || !environment?.ready || !$("review-confirmed").checked; }
  function updatePrepare() {
    $("prepare-review").disabled = $("validate-import").disabled = creating || preparing || readingSpec;
    $("validate-import").textContent = readingSpec ? "Reading file…" : preparing ? "Checking configuration…" : "Validate & review configuration";
  }
  function renderEnvironment(pending = false, timedOut = false) {
    const ready = environment?.ready === true;
    $("environment-badge").textContent = ready ? "Environment ready" : pending ? "Checking environment…" : "Environment needs attention";
    $("environment-badge").className = "environment-badge " + (ready ? "ready" : pending ? "" : "attention");
    $("environment-summary").textContent = ready ? "The Lab and owned Studio are ready for your test."
      : pending ? "Checking the owned Studio runtime. You can prepare and review your test while this finishes."
      : timedOut ? "The Studio check is taking longer than expected. Your draft is kept; use Check again or inspect the setup terminal."
      : "You can prepare and review a test now. Check the environment details before creating it.";
    $("environment-checks").replaceChildren();
    for (const check of environment?.checks || []) {
      const card = node("div", "", "environment-check " + (check.status === "fail" ? "fail" : ""));
      card.append(node("strong", check.label), node("p", check.detail));
      if (check.command) card.append(node("code", check.command));
      $("environment-checks").append(card);
    }
    $("environment-help").open = !ready; updateCreate(); renderConnection();
  }
  function checkEnvironment() {
    if (environmentCheck) return environmentCheck;
    $("check-environment").disabled = true;
    environment = environment ? {...environment, ready:false, checks:[]} : null;
    renderEnvironment(true);
    const authorization = token, deadline = Date.now() + 45000;
    environmentCheck = (async () => {
      try {
        for (;;) {
          if (authorization !== token) throw new Error("Workspace changed");
          const remaining = deadline - Date.now();
          if (remaining <= 0) { renderEnvironment(false, true); return environment; }
          environment = await request("/v1/onboarding/status", "GET", undefined, Math.min(30000, remaining));
          const pending = !environment.ready && (environment.checks || []).some(check => check.status === "pending")
            && !(environment.checks || []).some(check => check.status === "fail");
          renderEnvironment(pending);
          if (!pending) return environment;
          const delay = Math.min(2000, Math.max(0, deadline - Date.now()));
          if (delay) await new Promise(resolve => setTimeout(resolve, delay));
        }
      } catch (error) {
        environment = null; renderEnvironment();
        $("environment-summary").textContent = "The environment check could not finish. Check the Lab connection or setup terminal, then try again.";
        throw error;
      }
    })().finally(() => { environmentCheck = null; $("check-environment").disabled = false; updateCreate(); });
    return environmentCheck;
  }
  function category(template) { return template.id.startsWith("investigation-") ? "investigation" : template.id.includes("-messages-") ? "messages" : "decision"; }
  function renderTemplates() {
    $("templates").replaceChildren();
    const filter = $("template-filter").value;
    for (const template of templates.filter(item => filter === "all" || category(item) === filter)) {
      const button = node("button", "", "template-card"); button.type = "button"; button.disabled = creating;
      button.dataset.template = template.id;
      button.setAttribute("aria-pressed", String(chosen?.id === template.id));
      button.append(node("span", {decision:"Decisions & appeals", investigation:"Evidence & investigation", messages:"Actions across contracts"}[category(template)], "template-category"),
        node("strong", template.title), node("p", template.description));
      button.addEventListener("click", () => choose(template)); $("templates").append(button);
    }
  }
  function choose(template) {
    if (creating) return;
    invalidate(); chosen = template; renderTemplates(); $("template-form").hidden = false;
    $("chosen-title").textContent = template.title; $("chosen-description").textContent = template.description;
    $("template-fields").replaceChildren();
    for (const field of template.fields.slice(0, 5)) {
      const wrapper = node("div", "", field.type === "textarea" ? "wide" : "");
      const input = document.createElement(field.type === "textarea" ? "textarea" : field.type === "select" ? "select" : "input");
      input.id = "field-" + field.name; input.name = field.name; input.required = true;
      if (field.type === "select") for (const option of field.options || []) { const item = node("option", option.label); item.value = option.value; input.append(item); }
      else if (field.type !== "textarea") input.type = field.type === "number" ? "number" : "text";
      if (field.type === "number") { input.step = field.name === "initial_confidence_bps" ? "0.01" : "1"; input.min = field.name === "timeout_seconds" ? "1" : "0"; input.max = field.name === "timeout_seconds" ? "30" : "100"; }
      else if (field.type !== "select") input.maxLength = field.name === "title" ? 160 : 4000;
      input.value = field.name === "timeout_seconds" ? "30" : field.name === "initial_confidence_bps" ? String(field.default / 100) : String(field.default ?? "");
      const label = node("label", field.name === "initial_confidence_bps" ? "Initial model confidence (%)" : field.name === "timeout_seconds" ? "Time to complete the test (minutes)" : field.label); label.htmlFor = input.id;
      const help = node("p", field.name === "timeout_seconds" ? "Up to 30 minutes after your agent first observes the ready test. Setup has a separate 60-minute limit." : field.name === "initial_confidence_bps" ? "0–100%. An upheld appeal keeps the same response." : field.help || "", "helper");
      help.id = input.id + "-help"; input.setAttribute("aria-describedby", help.id);
      input.addEventListener("input", invalidate); input.addEventListener("change", invalidate);
      wrapper.append(label, input, help); $("template-fields").append(wrapper);
    }
  }
  const relation = {eq:"equals", ne:"differs from", lt:"is less than", lte:"is at most", gt:"is greater than", gte:"is at least", in:"is one of", contains:"contains"};
  function ruleDescription(rule) {
    if (rule.op === "exists") return rule.label + " — must be present.";
    const right = rule.right;
    const value = right && Object.hasOwn(right, "literal") ? text(right.literal)
      : right ? words(right.path).replaceAll(".", " → ") + " from " + right.source : "the reviewed value";
    return `${rule.label} — ${relation[rule.op] || rule.op} ${value}.`;
  }
  function renderReview(value) {
    preview = value; const spec = value.spec; $("review-confirmed").checked = false;
    $("spec").value = pretty(spec);
    $("review-summary").replaceChildren(node("li", spec.title), node("li", spec.task),
      node("li", `Contracts: ${Object.keys(spec.project_snapshot.definition.contracts).map(words).join(", ")}.`),
      node("li", `Time limit: ${Math.round(spec.timeout_seconds / 60)} minutes (${spec.timeout_seconds} seconds).`));
    if (Object.keys(spec.context || {}).length) {
      const item = node("li", "Public context"); item.append(node("pre", pretty(spec.context))); $("review-summary").append(item);
    }
    for (const evidence of spec.evidence || []) {
      const item = node("li", ""); item.append(node("strong", evidence.title), node("p", evidence.content));
      if (evidence.provenance) item.append(node("p", evidence.provenance, "helper"));
      if (evidence.data) item.append(node("pre", pretty(evidence.data)));
      $("review-summary").append(item);
    }
    $("review-responses").replaceChildren();
    for (const [phase, responses] of Object.entries(spec.fixtures || {})) for (const response of responses) {
      const card = node("div", "", "response-card"); card.append(node("h4", words(phase) + " response"));
      if (response.response && typeof response.response === "object" && !Array.isArray(response.response)) {
        const list = node("dl"); for (const [name, value] of Object.entries(response.response)) list.append(node("dt", name === "confidence_bps" ? "Confidence" : words(name)), node("dd", name === "confidence_bps" && typeof value === "number" ? `${value / 100}%` : text(value))); card.append(list);
      } else card.append(node("p", text(response.response)));
      $("review-responses").append(card);
    }
    if (!$("review-responses").children.length) $("review-responses").append(node("p", "No supplied contract response override."));
    const rules = $("review-rules"); rules.replaceChildren();
    for (const rule of spec.expectations.rules) rules.append(node("li", ruleDescription(rule)));
    for (const action of spec.expectations.required_actions || []) rules.append(node("li", `${words(action.operation)}: ${action.min_count} to ${action.max_count ?? "any number of"} ${action.successful ? "successful calls" : "attempts"}.`));
    for (const action of spec.expectations.forbidden_actions || []) rules.append(node("li", `Must not attempt ${words(action)}.`));
    if (spec.expectations.require_finalized) rules.append(node("li", "Submitted transactions must finalize before completion."));
    rules.append(node("li", spec.policy.allow_appeal ? `Appeals permitted: at most ${spec.policy.max_appeals}.` : "Appeals are not permitted."));
    for (const [name, policy] of Object.entries(spec.policy.operations)) {
      rules.append(node("li", `${words(name)}: at most ${policy.max_calls} calls${policy.require_finalized.length ? "; wait for finalized " + policy.require_finalized.map(words).join(", ") : ""}.`));
      for (const rule of policy.constraints) rules.append(node("li", `${words(name)}: ${ruleDescription(rule)}`));
      if (policy.max_fee !== null) rules.append(node("li", `${words(name)} fee limit: ${text(policy.max_fee)} local GEN base units.`));
    }
    for (const rule of spec.policy.appeal_constraints || []) rules.append(node("li", "Before an appeal: " + ruleDescription(rule)));
    for (const field of ["max_fee", "max_total_fee"]) if (spec.policy[field] !== null) rules.append(node("li", `${words(field)}: ${text(spec.policy[field])} local GEN base units.`));
    $("review-warnings").replaceChildren(...(value.warnings || []).map(item => node("p", item, "helper")));
    $("review-spec").textContent = pretty({digest:value.digest, spec, explanation:value.summary, rules:value.rules});
    $("review-duration").textContent = "You have up to 60 minutes to prepare Studio and connect your agent. The selected test time starts when the agent first observes the ready test.";
    $("review-panel").hidden = false; step("review"); updateCreate(); $("review-panel").scrollIntoView({behavior:"smooth", block:"start"});
  }
  async function prepare(kind) {
    if (creating || preparing || readingSpec) return;
    clearNotice(); clearImportError(); const current = ++revision; preview = null; $("review-panel").hidden = true; $("review-confirmed").checked = false; updateCreate(); preparing = true; updatePrepare();
    try {
      let result;
      if (kind === "template") {
        const values = Object.fromEntries(chosen.fields.map(field => {
          const input = $("field-" + field.name);
          return [field.name, field.name === "timeout_seconds" ? Number(input.value) * 60
            : field.name === "initial_confidence_bps" ? Math.round(Number(input.value) * 100)
            : field.type === "number" ? Number(input.value) : input.value];
        }));
        result = await request("/v1/onboarding/draft", "POST", {template_id:chosen.id, values});
      } else {
        if (new TextEncoder().encode($("spec").value).length > 2400000) throw new Error("The scenario exceeds the supported file size.");
        const spec = importSpec($("spec").value);
        result = await request("/v1/onboarding/preview", "POST", {spec});
      }
      if (current === revision) renderReview(result);
    } catch (error) {
      if (current === revision) fail(error, kind === "advanced" ? "import-error" : "notice");
    } finally {
      preparing = false; updatePrepare();
    }
  }
  function editable(disabled) {
    for (const input of document.querySelectorAll("#builder input,#builder textarea,#builder select,#builder button,#edit-test,#new-test")) input.disabled = disabled;
    $("reviewer").disabled = disabled; $("review-confirmed").disabled = disabled;
    $("test-mode").disabled = disabled;
    updatePrepare();
  }
  async function createRun() {
    if (creating || !preview || !$("review-confirmed").checked) return;
    clearNotice(); creating = true; editable(true); updateCreate();
    const current = revision, reviewed = preview;
    let creationAttempted = false, createdResponseReceived = false;
    try {
      await checkEnvironment();
      if (!environment?.ready) throw new Error("The environment is not ready yet. Your reviewed draft is kept here; check the environment details and try again.");
      const approved = await request("/v1/onboarding/review", "POST", {spec:reviewed.spec, expected_sha256:reviewed.digest, reviewer:$("reviewer").value.trim()});
      if (current !== revision) throw new Error("The test changed. Review its current content before creating it.");
      creationAttempted = true;
      const expiresAt = Date.now() + approved.spec.timeout_seconds * 1000;
      const created = await request("/v1/workflows", "POST", {spec:approved.spec, ...(approved.spec.project_snapshot ? {wait_for_agent:true} : {})});
      createdResponseReceived = true;
      selectedQuick = null; quickTests?.hideRun(); selected = created.run_id; names.set(selected, approved.spec.title);
      credentials.set(selected, {token:created.agent_token, expiresAt});
      updateTiming(created);
      preview = null; $("review-panel").hidden = true; $("builder").hidden = true;
      step("connect"); renderConnection(); await refresh();
      $("connection").scrollIntoView({behavior:"smooth", block:"start"});
    } catch (error) {
      if (createdResponseReceived) { renderConnection(); feedback("Your test was created and its connection settings are available. Refresh failed; use Check connection or Refresh to try again."); }
      else if (creationAttempted && error.uncertain) { preview = null; $("review-confirmed").checked = false; feedback("Check Your tests before creating another run: a lost creation response may still have created the test. Its key cannot be recovered here."); await refresh().catch(() => {}); }
      throw error;
    } finally { creating = false; editable(false); updateCreate(); }
  }
  function agentUrl() {
    const supplied = $("agent-location").value === "tunnel" ? $("agent-url").value : environment?.server_url;
    const url = new URL(supplied || "");
    if (!["http:", "https:"].includes(url.protocol) || !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
      || url.username || url.password || url.search || url.hash || url.pathname !== "/") throw new Error("Enter the loopback Lab address reachable by your agent, including the correct port.");
    return url.origin;
  }
  function updateTiming(run) {
    if (!run.timing) return;
    const timing = run.timing, remaining = Number(timing.seconds_remaining);
    const deadline = timing.deadline_at ?? timing.setup_deadline_at;
    const expiresAt = timing.seconds_remaining != null && Number.isFinite(remaining)
      ? Date.now() + Math.max(0, remaining) * 1000 : Number(deadline) * 1000;
    timings.set(run.run_id, {...timing, expiresAt});
    const saved = credentials.get(run.run_id);
    if (saved && Number.isFinite(expiresAt)) saved.expiresAt = expiresAt;
  }
  function timingText(id) {
    const timing = timings.get(id);
    if (!timing) return "";
    if (timing.phase === "ended") return "The test has ended.";
    const seconds = Math.max(0, Math.ceil((timing.expiresAt - Date.now()) / 1000));
    const remaining = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    return timing.phase === "setup" ? `Setup: ${remaining} remaining of the 60-minute setup limit. The test timer starts when your agent first observes the ready test.`
      : `Test time: ${remaining} remaining. Started when your agent observed the ready test.`;
  }
  function renderConnection() {
    if (connectionRun !== selected) {
      connectionRun = selected; connectionRevision++;
      $("connection-badge").textContent = "Waiting for agent";
      $("connection-badge").className = "environment-badge";
      $("connection-status").textContent = "Connection has not been checked for this test yet.";
      $("check-connection").disabled = false;
    }
    let saved = credentials.get(selected);
    if (saved?.expiresAt <= Date.now()) { credentials.delete(selected); saved = null; }
    $("connection").hidden = !saved;
    $("copy-setup-prompt").disabled = $("copy-config").disabled = true;
    if (!saved) { $("credential").value = ""; clearSetupPrompt(); return; }
    $("connection-run").textContent = `${names.get(selected) || "Agent test"} · ${selected}`;
    updateText("connection-timing", timingText(selected) || "The test timer is running. Connect your agent promptly.");
    const client = $("agent-client").value, remote = $("agent-location").value === "tunnel";
    $("remote-settings").hidden = !remote; $("remote-mcp").hidden = !remote || client !== "mcp";
    try {
      const url = agentUrl(), run = selected, secret = saved.token;
      let content;
      if (client === "mcp") {
        const command = remote ? $("mcp-path").value.trim() : environment?.mcp_command;
        if (!command || !/^(?:\/|[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+)/.test(command)) throw new Error("Enter the full installed MCP executable path on the computer where your agent runs.");
        content = JSON.stringify({mcpServers:{"genlayer-lab":{command, args:[], env:{LAB_URL:url, LAB_MODE:"workflow", LAB_ROLE:"agent", LAB_RUN_ID:run, LAB_TOKEN:secret}}}}, null, 2);
        $("connection-instructions").textContent = "Add this connector in your agent host's MCP settings. For OpenClaw, restart the Gateway from the server terminal and open a fresh chat with the same agent, then use Copy start prompt. The connector runs beside your agent.";
      } else if (client === "python") {
        content = `from genlayer_agent_lab.client import LabClient\n\nwith LabClient(${JSON.stringify(url)}, ${JSON.stringify(secret)}) as lab:\n    run_id = ${JSON.stringify(run)}\n    observation = lab.workflow_observe(run_id)\n    print(observation)\n    # Supply observation to your agent. Let its policy invoke declared\n    # operations, appeal when permitted, and finish when work is complete.\n    # lab.workflow_invoke(run_id, operation, arguments, idempotency_key, decision_id)\n    # lab.workflow_appeal(run_id, idempotency_key, decision_id)\n    # lab.workflow_finish(run_id)\n`;
        $("connection-instructions").textContent = "Use the installed Python client in your agent's environment. This connection example reads the task; add your agent's policy loop to complete it.";
      } else if (client === "typescript") {
        content = `// Use client.ts from the exported Lab kit's examples/typescript directory.\nimport {LabClient} from './client.ts';\nconst lab = new LabClient(${JSON.stringify(url)}, ${JSON.stringify(secret)});\nconst runId = ${JSON.stringify(run)};\nconst observation = await lab.workflowObserve(runId);\nconsole.log(observation);\n// Pass observation to your agent's policy. The supplied client preserves\n// exact integer values for workflowInvoke, workflowAppeal and workflowFinish.\n`;
        $("connection-instructions").textContent = "Use the supplied TypeScript client (Node.js 24 or newer). This reads the task and preserves exact project integers; connect your policy to its workflow methods.";
      } else {
        content = `Lab URL: ${url}\nRun ID: ${run}\nAuthorization: Bearer ${secret}\nContent-Type: application/json\n\nPOST /v1/workflows/${run}/observe\nPOST /v1/workflows/${run}/operations\n  {"operation":"<declared operation>","arguments":{},"idempotency_key":"<stable key>","expected_decision_id":"<observed decision>"}\nPOST /v1/workflows/${run}/appeals\n  {"idempotency_key":"<stable key>","expected_decision_id":"<observed decision>"}\nPOST /v1/workflows/${run}/finish\n\nProject integers outside the safe JSON number range use {"$lab_integer":"<decimal>"}.\nUse the supplied clients for exact encoding and reserved-object escaping.\n`;
        $("connection-instructions").textContent = "Send a run-authenticated observe request first. Use the returned task and method schemas, preserve retry keys, and follow the project's exact-integer wire format.";
      }
      if ($("credential").value !== content) clearSetupPrompt();
      $("credential").value = content; $("copy-setup-prompt").disabled = $("copy-config").disabled = false;
    } catch (error) { $("credential").value = ""; clearSetupPrompt(); $("connection-instructions").textContent = error.message; }
  }
  function clearSetupPrompt() { $("setup-prompt").value = ""; $("setup-prompt-details").open = false; }
  function setupPrompt() {
    renderConnection();
    if ($("copy-setup-prompt").disabled) throw new Error("Complete the connection settings for an active test first.");
    const client = $("agent-client").value;
    const configure = client === "mcp"
      ? "Identify your installed agent host; inspect its CLI help and configuration schema. Adapt the generic mcpServers entry below to its native configuration (OpenClaw uses mcp.servers in current versions). Use the exact absolute executable path, arguments and environment below. This is a stdio MCP connector beside your agent; LAB_URL is the Lab API, not an HTTP MCP endpoint. Apply it in the actual running agent session or gateway. For OpenClaw, openclaw mcp reload affects only that CLI process; it does not reload the running Gateway or Codex session. After saving the connector, have the operator run openclaw gateway restart in the server terminal, verify openclaw gateway status, then open a fresh chat with this same named agent. Stop at that restart boundary and give those exact remaining steps; do not claim the current session was refreshed. In the fresh chat, use the runtime's native tool search to discover this server and call the connector's actual observe tool for this run using the returned name and schema. Tool names and namespaces depend on the host; do not require a hardcoded genlayer-lab__observe name. If discovery still fails, inspect the saved server, any codex.agents scope and Gateway diagnostics once, then report the missing connection. Do not repeatedly spawn child agents or use MCP Apps/view APIs to discover ordinary MCP tools. A saved configuration, CLI probe, reload, tool listing or separate HTTP request does not prove this session can use MCP."
      : client === "python"
        ? "Use the installed Python LabClient in your actual agent runtime with the exact URL, run ID and test key below. Connect its workflow methods to your agent's policy loop and call workflow_observe for this run. The example alone does not complete the task."
        : client === "typescript"
          ? "Use the supplied TypeScript LabClient from the existing exported Lab kit in your actual agent runtime (Node.js 24 or newer). Locate that existing client before adapting the import below; preserve its exact-integer encoding. Connect its workflow methods to your agent's policy loop and call workflowObserve for this run. The example alone does not complete the task."
          : "Configure your agent's HTTP tools with the exact URL, run ID and run-scoped Bearer key below. Make a real authenticated POST observe request through those tools before acting. Preserve exact project integers using the supplied wire format and reserved-object escaping.";
    return [
      "Connect yourself to this already-created, developer-reviewed GenLayer Agent Lab test and carry out its public task; do not create another run. " + (timings.get(selected)?.phase === "setup" ? "Setup has a separate 60-minute limit. Your first authenticated observe starts the selected test timer once Studio is ready; configuration and tool discovery alone do not start it. " : "Its test timer is running. ") + "Do not install OpenClaw, rebuild the Lab or launch a scripted reference agent.",
      configure,
      "Preserve your chosen model/provider, permission settings and unrelated configuration. Do not broaden tool permissions or change models to fix discovery. If shell, configuration or tool access is unavailable, or a connector/client is missing, give the exact remaining manual step and stop without claiming completion. Report an expired run without replacing it.",
      "Use only the run credential below. Never obtain or use administrator/workspace keys, private scenario files, hidden responses, grading rules or reference-agent code. Keep this key out of replies, command logs, URLs and reports; store it only in the intended local connection settings with restricted access.",
      "After successful observation through the selected connector, read the public task, evidence, permissions and method schemas, including builtin_operations when supplied. Follow each operation's arguments_schema and example exactly; read_evidence takes an id field. Use your own reasoning and route tested actions through the Lab. Preserve idempotency keys when retrying the same request and observed decision IDs. If a request is rejected, read its error_detail and use a new idempotency key for corrected arguments; observe until submitted work settles, and invoke finish only after the task is complete. Avoid production wallet or contract tools.",
      "Summarize configuration, actual observe result, actions and finish result without keys. Report failures and remaining steps honestly; setup alone does not establish a pass.",
      "Selected connection settings (contains this run's private test key):\n" + $("credential").value,
    ].join("\n\n");
  }
  async function copySetupPrompt() {
    const value = setupPrompt(), run = selected;
    try {
      await navigator.clipboard.writeText(value);
      if (selected === run && credentials.has(run)) feedback("Copied. Paste the setup prompt into your agent.");
    } catch {
      // This prompt contains a credential: never echo it into notices or feedback.
      if (selected !== run || !credentials.has(run)) return;
      const current = setupPrompt();
      if (current !== value) return;
      $("setup-prompt").value = current; $("setup-prompt-details").open = true;
      $("setup-prompt").focus(); $("setup-prompt").select();
      feedback("Clipboard access is unavailable. Copy the selected setup prompt manually.");
    }
  }
  async function checkConnection() {
    if (!selected) return;
    const id = selected, serial = ++connectionRevision; $("check-connection").disabled = true;
    try {
      const result = await request("/v1/onboarding/connection/" + encodeURIComponent(id));
      if (selected !== id || serial !== connectionRevision) return;
      $("connection-badge").textContent = result.connected ? "Agent activity observed" : "Waiting for agent";
      $("connection-badge").className = "environment-badge " + (result.connected ? "ready" : "");
      $("connection-status").textContent = result.detail + (result.last_seen ? ` Last request: ${new Date(result.last_seen).toLocaleString()}. This does not indicate a live agent process.` : " Start your agent with the copied settings.");
    } finally { if (selected === id && serial === connectionRevision) $("check-connection").disabled = false; }
  }
  async function copy(value) {
    if (!value) throw new Error("Complete the connection settings first.");
    try { await navigator.clipboard.writeText(value); feedback("Copied. Paste it into your agent's test configuration."); }
    catch {
      if (value !== $("credential").value) { feedback("Copy this start prompt manually: " + value); return; }
      $("credential").closest("details").open = true; $("credential").focus(); $("credential").select();
      throw new Error("Clipboard access is unavailable. Select and copy the displayed connection settings manually.");
    }
  }
  function updateText(id, value) { if ($(id).textContent !== value) $(id).textContent = value; }
  function syncReportItems(container, values, identify, build) {
    const previous = new Map(Array.from(container.children, item => [item.dataset.reportKey, item]));
    const keep = new Set();
    values.forEach((value, index) => {
      const key = identify(value, index), signature = pretty(value);
      let item = previous.get(key);
      if (!item || item.dataset.renderSignature !== signature) {
        item = build(value, index); item.dataset.reportKey = key; item.dataset.renderSignature = signature;
      }
      keep.add(item);
      if (container.children[index] !== item) container.insertBefore(item, container.children[index] || null);
    });
    for (const item of Array.from(container.children)) if (!keep.has(item)) item.remove();
  }
  function reportView() {
    const root = $("detail"), opened = new Map(Array.from(root.querySelectorAll("details[data-disclosure]"), item => [item.dataset.disclosure, item.open]));
    const selection = document.getSelection();
    function point(target, offset) {
      if (!target || !root.contains(target)) return null;
      const parent = target.nodeType === Node.TEXT_NODE ? target.parentElement : target;
      const scope = parent.closest("[data-report-key], [id]");
      const path = []; let current = target;
      while (current !== scope) { path.unshift(Array.prototype.indexOf.call(current.parentNode.childNodes, current)); current = current.parentNode; }
      return {id:scope.id, key:scope.dataset.reportKey, path, offset};
    }
    const anchor = point(selection?.anchorNode, selection?.anchorOffset), focus = point(selection?.focusNode, selection?.focusOffset);
    const selectedText = selection?.toString();
    return () => {
      for (const item of root.querySelectorAll("details[data-disclosure]")) if (opened.has(item.dataset.disclosure)) item.open = opened.get(item.dataset.disclosure);
      if (!anchor || !focus || !selectedText || document.getSelection()?.toString() === selectedText) return;
      function resolve(saved) {
        let target = saved.id ? $(saved.id) : root.querySelector(`[data-report-key="${CSS.escape(saved.key)}"]`);
        for (const index of saved.path) target = target?.childNodes[index];
        return target ? [target, Math.min(saved.offset, target.nodeType === Node.TEXT_NODE ? target.length : target.childNodes.length)] : null;
      }
      const a = resolve(anchor), f = resolve(focus);
      if (a && f) document.getSelection().setBaseAndExtent(...a, ...f);
    };
  }
  function renderInvestigations(value) {
    const investigations = value.investigations || []; $("investigation-detail").hidden = !investigations.length;
    syncReportItems($("investigations"), investigations, (submission, index) => "investigation:" + (submission.submission_id || submission.idempotency_key || index), (submission, index) => {
      const card = node("article", "", "investigation-card");
      card.append(node("h4", {accept:"Accept the decision", appeal:"Challenge the decision", request_review:"Request review"}[submission.disposition] || "Submitted findings"), node("p", submission.summary), node("p", "Proposed result: " + text(submission.proposed_result)));
      const list = node("ul", "", "investigation-findings");
      for (const finding of submission.findings || []) { const item = node("li"); item.append(node("strong", `${words(finding.evidence_id)}: ${words(finding.assessment)}`), node("p", finding.note)); list.append(item); }
      card.append(list);
      const detail = node("details"); detail.append(node("summary", "Decision and evidence references"), node("pre", pretty({decision_id:submission.decision_id ?? null, citations:submission.citations || [], decision_snapshot:submission.decision_snapshot ?? null})));
      detail.dataset.disclosure = "investigation:" + (submission.submission_id || submission.idempotency_key || index);
      card.append(detail); return card;
    });
  }
  function reportChecks(value) {
    if (Array.isArray(value.checks)) return value.checks;
    const labels = {decision:"Decision and finality", behavior:"Agent respected the permitted actions", outcome:"Final application outcome", completion:"Required work completed"};
    const details = {decision:"Checks the finalized, successfully executed decision against the reviewed expected values.",
      behavior:value.behavior_failures?.length ? "Recorded issues: " + value.behavior_failures.map(words).join(", ") : "No action-policy failure was recorded.",
      outcome:"Compares the observed application state with the reviewed final state.",
      completion:"Required actions: " + (value.expectations?.required_actions || []).map(words).join(", ") + ". The agent must also finish the run."};
    return Object.entries(value.grades || {}).map(([id, grade]) => ({id, label:labels[id] || words(id), outcome:grade.status, detail:grade.detail || details[id] || "See the recorded evidence for this check."}));
  }
  function checkSummary(value) {
    const counts = {pass:0, fail:0, inconclusive:0};
    for (const check of reportChecks(value)) if (Object.hasOwn(counts, check.outcome)) counts[check.outcome]++;
    return Object.entries(counts).filter(([, count]) => count).map(([outcome, count]) => `${count} ${count === 1 ? "check" : "checks"} ${outcome === "pass" ? "passed" : outcome === "fail" ? "failed" : "inconclusive"}`).join(" · ");
  }
  function policyOverlap(value) {
    const checks = reportChecks(value);
    if (!["policy_compliance", "behavior"].every(id => checks.some(check => check.id === id && check.outcome === "fail"))) return "";
    const rejected = (value.operations || []).filter(operation => operation.status === "rejected");
    if (!rejected.length) return "";
    return `The two policy checks overlap: both include the same ${rejected.length === 1 ? "rejected request" : rejected.length + " rejected requests"} below. Two failed checks do not necessarily mean two separate mistakes. Completing later steps does not remove earlier rejected requests from the report.`;
  }
  function renderReport(run, result) {
    const ended = terminal.has(run.status); report = ended ? result : null;
    if (ended || run.status === "closing") credentials.delete(run.run_id);
    updateText("result-timing", timingText(run.run_id));
    const stableTiming = value => {
      if (!value.timing) return value;
      const {seconds_remaining, ...timing} = value.timing;
      return {...value, timing};
    };
    const signature = pretty({run:stableTiming(run), result:stableTiming(result)});
    if (renderedReport?.id === run.run_id && renderedReport.signature === signature && !$("detail").hidden) return;
    const restoreView = renderedReport?.id === run.run_id ? reportView() : () => {};
    renderedReport = {id:run.run_id, signature};
    const grade = ended ? result.verification || "inconclusive" : "running";
    names.set(run.run_id, run.title || names.get(run.run_id) || run.run_id);
    $("detail").hidden = false; $("title").textContent = names.get(run.run_id);
    $("status").textContent = `${words(run.status)} · Studio workflow · ${run.run_id}`; $("cancel").hidden = ended;
    $("download-readable").disabled = $("download").disabled = !ended;
    const labels = {pass:["All reviewed checks passed", "The recorded actions and results passed this test's reviewed checks."], fail:["Some reviewed checks failed", "Read the failed checks and the actions below to see what differed from your expectations."], inconclusive:["This test could not establish a complete result", "Inspect the environment and execution details before judging the agent."], running:["Your test is in progress", "Connect your agent if you have not already. Results appear after the run has finished and Studio observations are settled."]};
    const explanation = labels[grade] || labels.inconclusive;
    $("result-summary").className = "result-summary " + (Object.hasOwn(labels, grade) ? grade : "inconclusive");
    $("result-summary").replaceChildren(node("h3", explanation[0]), node("p", explanation[1]));
    if (ended) {
      $("result-summary").append(node("p", checkSummary(result), "check-counts"));
      if (run.status === "completed") $("result-summary").append(node("p", "Test completed means the run finished. The checks above show whether it met the reviewed expectations.", "helper"));
      const overlap = policyOverlap(result); if (overlap) $("result-summary").append(node("p", overlap, "policy-explanation"));
    }
    if (run.error_code) $("result-summary").append(node("p", "Diagnostic: " + run.error_code));
    syncReportItems($("evaluation-cards"), ended ? reportChecks(result) : [], check => "check:" + check.id, check => {
      const card = node("article", "", "check-card " + (check.outcome === "fail" ? "fail" : ""));
      const heading = node("div", "", "check-heading"); heading.append(node("strong", check.label || words(check.id)), node("span", words(check.outcome), "pill"));
      card.append(heading, node("p", text(check.detail ?? ""))); return card;
    });
    syncReportItems($("operations"), run.operations || [], (operation, index) => "operation:" + (operation.idempotency_key || index), (operation, index) => {
      const item = node("li"); item.append(node("strong", `${words(operation.operation)} · ${words(operation.status)}`));
      if (operation.error_detail) item.append(node("p", operation.error_detail, "operation-error"));
      if (operation.error_code) item.append(node("p", "Diagnostic: " + operation.error_code));
      else item.append(node("p", operation.tx_id ? `Studio transaction ${operation.tx_id}` : "Recorded Lab tool request."));
      const detail = node("details"); detail.dataset.disclosure = "operation:" + (operation.idempotency_key || index);
      detail.append(node("summary", "Operation details"), node("pre", pretty(operation))); item.append(detail); return item;
    });
    if (!$("operations").children.length) $("operations").append(node("li", "No agent operation has been recorded yet."));
    renderInvestigations(result);
    $("project-detail").hidden = run.profile !== "project";
    updateText("contracts", pretty(run.contracts || {}));
    updateText("accounting", pretty({reserved_deposits:run.fee_reserved ?? null, setup_deposits:result.setup_fee_reserved ?? null, final_balance:result.final_balance ?? null, unit:run.fee_unit ?? null, note:result.fee_accounting_note ?? null}));
    updateText("recovery", pretty({cleanup:result.cleanup ?? null, scope:result.recovery_scope ?? null}));
    updateText("decision", pretty(run.transactions || run.decision || {})); updateText("state", pretty(run.state || {}));
    updateText("evaluation", ended ? pretty({verification:result.verification ?? null, checks:reportChecks(result), cleanup:result.cleanup ?? null}) : "Evaluation will be available after completion.");
    updateText("evidence", pretty(result));
    restoreView();
    if (ended) step("results");
  }
  async function loadSelected() {
    if (selectedQuick) { await quickTests.refreshRun(); return; }
    if (!selected) return;
    const id = selected, serial = ++detailRevision;
    const run = await request("/v1/workflows/" + encodeURIComponent(id));
    const result = terminal.has(run.status) ? await request(`/v1/workflows/${encodeURIComponent(id)}/report`) : run;
    if (selected !== id || serial !== detailRevision) return;
    updateTiming(result);
    renderReport(run, result); renderConnection(); await checkConnection();
  }
  function scenarioBackend(run) {
    return {glsim:"GLSim", "container-glsim":"GLSim · custom binding", studio:"Earlier Studio scenario", fixture:"Fixture-only scenario"}[run.backend] || "Earlier scenario · engine not recorded";
  }
  function renderHistory() {
    const filter = $("history-mode").value;
    const createdTime = value => typeof value === "number" ? value * 1000 : Date.parse(value) || 0;
    const runs = [...workflowHistory.map(run => ({...run, source:"workflow"})), ...scenarioHistory.map(run => ({...run, source:"scenario"}))]
      .sort((a, b) => createdTime(b.created_at) - createdTime(a.created_at));
    $("runs").replaceChildren();
    for (const run of runs.filter(run => filter === "all" || filter === run.source)) {
      const active = run.source === "workflow" ? selected === run.run_id : selectedQuick === run.run_id;
      const button = node("button", "", "run-card secondary" + (active ? " selected" : ""));
      button.dataset.source = run.source; button.dataset.runId = run.run_id;
      const label = node("span");
      label.append(node("strong", run.source === "workflow" ? names.get(run.run_id) || run.title || "Agent test" : run.title || words(run.scenario_id) || "Scenario test"),
        node("small", (run.source === "workflow" ? "Studio workflow" : scenarioBackend(run)) + " · " + run.run_id));
      button.append(label, node("span", words(run.status), "pill"));
      button.addEventListener("click", () => {
        if (creating || quickTests?.busy()) return;
        report = null; detailRevision++;
        if (run.source === "scenario") {
          selected = null; selectedQuick = run.run_id; $("detail").hidden = true; renderConnection();
          quickTests.showRun(run.run_id).catch(fail);
        } else {
          selectedQuick = null; quickTests.hideRun(); selected = run.run_id; changeMode("studio"); renderConnection();
          loadSelected().then(() => { if (selected === run.run_id) $("detail").scrollIntoView({behavior:"smooth", block:"start"}); }).catch(fail);
        }
        renderHistory();
      }); $("runs").append(button);
    }
    if (!$("runs").children.length) $("runs").append(node("p", "No tests in this view yet. Choose a mode and situation above to begin.", "empty"));
  }
  async function changeMode(value) {
    if (creating || quickTests?.busy()) { $("test-mode").value = testMode; return; }
    testMode = value; $("test-mode").value = value;
    $("studio-tests").hidden = value !== "studio"; $("quick-builder").hidden = value !== "glsim";
    $("test-mode-help").textContent = value === "studio"
      ? "Studio executes your contract and local transactions. Use this mode for project JSON imports, contract operations and appeals."
      : "GLSim runs supported quick checks with controlled responses and simulated consumer state. Appeals and project JSON imports require Studio execution.";
    if (value === "glsim") await quickTests.load();
  }
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      const results = await Promise.allSettled([request("/v1/workflows"), request("/v1/runs")]);
      if (!token) throw new Error("Please sign in again to view your tests.");
      if (results[0].status === "fulfilled") workflowHistory = results[0].value;
      if (results[1].status === "fulfilled") scenarioHistory = results[1].value;
      const unavailable = results.flatMap((result, index) => result.status === "rejected" ? [index ? "scenario" : "Studio workflow"] : []);
      $("history-error").hidden = !unavailable.length;
      $("history-error").textContent = unavailable.length ? "Could not refresh " + unavailable.join(" and ") + " history. Any previously shown entries are kept; use Refresh to try again." : "";
      renderHistory();
      await loadSelected();
      clearConnectionNotice();
    } finally { refreshing = false; }
  }
  async function connect() {
    const catalog = await request("/v1/onboarding/templates"); templates = catalog.templates;
    stopPairing();
    try { sessionStorage.setItem(key, token); } catch { /* Keep an in-memory session. */ }
    $("auth").hidden = true; $("workspace").hidden = false; $("disconnect").hidden = false; $("token").value = "";
    renderTemplates();
    await changeMode(testMode);
    await Promise.all([checkEnvironment().catch(error => { if (testMode === "studio") fail(error); }), refresh()]);
  }
  function stopPairing() {
    pairingRevision++; pairing = null; clearTimeout(pairingTimer);
    $("pair-panel").hidden = true; $("pair-prompt").value = "";
  }
  async function pairingRequest(path, body) {
    let response;
    try {
      response = await fetch(`/v1/dashboard/${path}`, {method:"POST", credentials:"omit", redirect:"error",
        cache:"no-store", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body),
        signal:AbortSignal.timeout(10000)});
    } catch { throw new Error("Your Lab is not responding. Check that your connection is still open, then try again."); }
    if (!response.ok) throw new Error(response.status === 429
      ? "Too many sign-in requests are waiting. Keep your current request open or try again in ten minutes."
      : response.status === 404 ? "This Lab needs an update for guided sign-in. Ask your setup agent to update the existing installation."
      : "This sign-in request is no longer available. Choose Connect this browser for a new one.");
    return response.json();
  }
  async function pollPairing(current) {
    if (current !== pairingRevision || !pairing) return;
    if (Date.now() >= pairing.deadline) {
      $("pair-status").textContent = "This request expired. Choose Connect this browser for a new one.";
      pairing = null; return;
    }
    try {
      const result = await pairingRequest("claim", {request_id:pairing.request_id, claim_secret:pairing.claim_secret});
      if (current !== pairingRevision) return;
      if (result.status === "approved") {
        token = result.token; stopPairing(); await connect(); return;
      }
    } catch (error) {
      if (current !== pairingRevision) return;
      $("pair-status").textContent = error.message;
      pairing = null; return;
    }
    pairingTimer = setTimeout(() => pollPairing(current), 2000);
  }
  $("pair-start").addEventListener("click", async () => {
    stopPairing(); clearNotice(); const current = pairingRevision;
    $("pair-start").disabled = true;
    try {
      const result = await pairingRequest("connect", {});
      if (current !== pairingRevision) return;
      pairing = {...result, deadline:Date.now() + result.expires_in * 1000};
      $("pair-prompt").value = `Let my browser into the GenLayer Agent Lab you installed. My sign-in code is ${result.code}. I authorize owner access for this browser. In the existing Lab installation on its host, run gl-agent-lab dashboard approve ${result.code}, using the same data directory and server port as setup (and uv run from a source checkout). Do not reinstall anything, read private test answers, or display any workspace keys. Tell me when approved so I can return to my browser.`;
      $("pair-command").textContent = `uv run gl-agent-lab dashboard approve ${result.code}`;
      $("pair-status").textContent = "Waiting for your setup agent's approval. This request lasts ten minutes.";
      $("pair-panel").hidden = false;
      pairingTimer = setTimeout(() => pollPairing(current), 2000);
    } catch (error) { fail(error); }
    finally { $("pair-start").disabled = false; }
  });
  $("pair-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("pair-prompt").value);
      $("pair-status").textContent = "Copied. Paste it into your setup agent, then return here.";
    } catch {
      $("pair-details").open = true;
      $("pair-prompt").focus(); $("pair-prompt").select();
      $("pair-status").textContent = "Copy the selected request and paste it into your setup agent.";
    }
  });
  function download(content, type, suffix) {
    const url = URL.createObjectURL(new Blob([content], {type})); const link = node("a"); link.href = url;
    link.download = `agent-test-${selected}.${suffix}`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function readableReport(value) {
    const escape = value => String(value ?? "").replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character]));
    const checks = reportChecks(value).map(check => `<li><strong>${escape(check.label)} — ${escape(check.outcome)}</strong><p>${escape(text(check.detail ?? ""))}</p></li>`).join("");
    const operations = (value.operations || []).map(item => `<li><strong>${escape(words(item.operation))} — ${escape(item.status)}</strong>${item.error_detail ? `<p>${escape(item.error_detail)}</p>` : ""}<p>${escape(item.error_code || item.tx_id || "Lab tool request")}</p></li>`).join("");
    const findings = (value.investigations || []).map(item => `<article><h3>${escape(words(item.disposition))}</h3><p>${escape(item.summary)}</p><p>Proposed result: ${escape(text(item.proposed_result))}</p><ul>${(item.findings || []).map(finding => `<li><b>${escape(finding.evidence_id)}: ${escape(finding.assessment)}</b> ${escape(finding.note)}</li>`).join("")}</ul></article>`).join("");
    const resultLabel = {pass:"All reviewed checks passed",fail:"Some reviewed checks failed",inconclusive:"The result is inconclusive"}[value.verification] || words(value.verification);
    const completion = value.status === "completed" ? "Test completed means the run finished. The checks show whether it met the reviewed expectations." : "";
    return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escape(value.title || "Agent test report")}</title><style>body{font:16px/1.6 system-ui,sans-serif;color:#243c3a;max-width:960px;margin:40px auto;padding:0 24px}h1,h2{line-height:1.2}li,article{padding:10px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f1;padding:18px}p{overflow-wrap:anywhere}@media print{details{display:none}}</style><h1>${escape(value.title || "Agent test report")}</h1><p>${escape(value.run_id)} · ${escape(value.status)}</p><h2>${escape(resultLabel)}</h2><p>${escape(checkSummary(value))}</p>${completion ? `<p>${escape(completion)}</p>` : ""}${policyOverlap(value) ? `<p>${escape(policyOverlap(value))}</p>` : ""}<p>${escape(value.task || "")}</p><ul>${checks}</ul><h2>What happened</h2><ol>${operations || "<li>No agent operation recorded.</li>"}</ol>${findings ? "<h2>Agent findings</h2>" + findings : ""}<details><summary>Advanced: full recorded report</summary><pre>${escape(pretty(value))}</pre></details><p>Local test evidence for the reviewed scenario. This report does not certify production safety.</p></html>`;
  }
  $("agent-url").value = location.origin;
  $("auth-form").addEventListener("submit", async event => { event.preventDefault(); clearNotice(); token = $("token").value.trim(); const button = event.submitter; if (button) button.disabled = true; try { await connect(); } catch (error) { fail(error); } finally { if (button) button.disabled = false; } });
  quickTests = window.createQuickTests({request, fail, feedback, getToken:() => token,
    onCreated:async run => {
      selected = null; selectedQuick = run.run_id; report = null; detailRevision++;
      $("detail").hidden = true; renderConnection(); await refresh();
    }, onChanged:() => refresh()});
  $("test-mode").addEventListener("change", event => changeMode(event.target.value).catch(fail));
  $("history-mode").addEventListener("change", renderHistory);
  $("disconnect").addEventListener("click", () => { try { sessionStorage.removeItem(key); } catch {} token = ""; quickTests.clear(); credentials.clear(); $("credential").value = ""; clearSetupPrompt(); $("copy-setup-prompt").disabled = $("copy-config").disabled = true; location.reload(); });
  $("template-filter").addEventListener("change", renderTemplates);
  $("open-contract-authoring").addEventListener("click", () => { $("custom-project").open = true; });
  $("copy-authoring-prompt").addEventListener("click", async () => {
    const field = $("authoring-prompt"), status = $("authoring-copy-status");
    try {
      await navigator.clipboard.writeText(field.value);
      status.textContent = "Copied. Paste this into your authoring agent and share your contract and test idea.";
    } catch {
      $("authoring-prompt-details").open = true;
      field.focus(); field.select();
      status.textContent = "Automatic copying is unavailable. Copy the selected prompt below and paste it into your authoring agent.";
    }
  });
  $("template-form").addEventListener("submit", event => { event.preventDefault(); prepare("template").catch(fail); });
  $("advanced-form").addEventListener("submit", event => { event.preventDefault(); prepare("advanced").catch(fail); });
  $("spec").addEventListener("input", invalidate);
  $("load-spec").addEventListener("change", async event => {
    const file = event.target.files[0]; if (!file) return; invalidate(); clearNotice(); $("spec").value = ""; const current = revision;
    const readSequence = ++specReadSequence; readingSpec = true; updatePrepare();
    try {
      if (file.size > 2400000) throw new Error("Scenario file exceeds the supported size.");
      const spec = importSpec(await file.text());
      if (current === revision) { $("spec").value = pretty(spec); feedback("Scenario loaded. Choose Validate & review configuration to review this exact file."); }
    } catch (error) { if (current === revision) fail(error, "import-error"); }
    finally { if (readSequence === specReadSequence) { readingSpec = false; updatePrepare(); } }
  });
  $("edit-test").addEventListener("click", () => { invalidate(); $("builder").hidden = false; $("builder").scrollIntoView({behavior:"smooth"}); });
  $("review-confirmed").addEventListener("change", updateCreate);
  $("create-form").addEventListener("submit", event => { event.preventDefault(); createRun().catch(fail); });
  $("check-environment").addEventListener("click", () => checkEnvironment().catch(fail));
  for (const id of ["agent-client", "agent-location", "agent-url", "mcp-path"]) $(id).addEventListener("input", renderConnection);
  $("copy-setup-prompt").addEventListener("click", () => copySetupPrompt().catch(fail));
  $("setup-prompt-details").addEventListener("toggle", () => {
    if (!$("setup-prompt-details").open) { $("setup-prompt").value = ""; return; }
    try { $("setup-prompt").value = setupPrompt(); } catch (error) { clearSetupPrompt(); fail(error); }
  });
  $("copy-config").addEventListener("click", () => copy($("credential").value).catch(fail));
  $("copy-agent-prompt").addEventListener("click", () => copy("Observe the connected GenLayer Agent Lab run. Follow its public task and permissions, read relevant evidence through the available tools, preserve idempotency keys on retries, and finish when the requested work is complete. Do not use production tools for these test actions.").catch(fail));
  $("check-connection").addEventListener("click", () => checkConnection().catch(fail));
  $("refresh").addEventListener("click", () => refresh().catch(fail));
  $("new-test").addEventListener("click", () => {
    if (creating || quickTests.busy()) return;
    invalidate(); selected = null; selectedQuick = null; report = null; detailRevision++; quickTests.hideRun();
    $("builder").hidden = false; $("detail").hidden = true; renderConnection(); renderHistory();
    $(testMode === "glsim" ? "quick-builder" : "builder").scrollIntoView({behavior:"smooth"});
  });
  $("cancel").addEventListener("click", async () => { if (!selected) return; $("cancel").disabled = true; try { await request(`/v1/workflows/${encodeURIComponent(selected)}/cancel`, "POST"); feedback("Stop requested. Submitted transactions may still be settling; their observed results will remain in the report."); await refresh(); } catch (error) { fail(error); } finally { $("cancel").disabled = false; } });
  $("download").addEventListener("click", () => { if (report) download(pretty(report), "application/json", "json"); });
  $("download-readable").addEventListener("click", () => { if (report) download(readableReport(report), "text/html", "html"); });
  setInterval(() => { if (token && !document.hidden && !$("workspace").hidden) { renderConnection(); refresh().catch(fail); } }, 5000);
  updateCreate(); if (token) connect().catch(fail);
})();
