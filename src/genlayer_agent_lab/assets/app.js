"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const activeStatuses = new Set(["queued", "preparing", "running"]);
  const attentionStatuses = new Set(["cancelled", "interrupted", "inconclusive"]);
  const storageKey = "genlayer-agent-lab-admin-token";
  let token = sessionStorage.getItem(storageKey) || "";
  let scenarios = [], bindings = [], runs = [], selected = null, currentReport = null, refreshing = false;
  const text = (tag, value, className) => {
    const node = document.createElement(tag);
    node.textContent = typeof value === "string" ? value : JSON.stringify(value);
    if (className) node.className = className;
    return node;
  };
  const pill = value => text("span", value || "pending", `pill ${["pass","fail","completed","queued","preparing","running","cancelled","interrupted","inconclusive"].includes(value) ? value : "neutral"}`);
  const showError = error => { $("notice").textContent = String(error.message || error).replaceAll(token || "\u0000", "[redacted]"); $("notice").hidden = false; };
  const clearError = () => { $("notice").hidden = true; };
  async function api(path, method = "GET", body) {
    const response = await fetch(path, {
      method, redirect: "error", signal: AbortSignal.timeout(30000),
      headers: {Authorization: `Bearer ${token}`, ...(body === undefined ? {} : {"Content-Type":"application/json"})},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({detail: "Request failed"}));
      throw new Error(`HTTP ${response.status}: ${typeof detail.detail === "string" ? detail.detail : JSON.stringify(detail.detail)}`);
    }
    return response;
  }
  const json = async (...args) => (await api(...args)).json();
  const scenarioName = run => {
    const id = run.scenario_id || (typeof run.scenario === "string" ? run.scenario : run.scenario?.id) || "Unknown scenario";
    const scenario = scenarios.find(item => (item.scenario_id || item.id) === id);
    return scenario?.name || scenario?.title || id;
  };
  function updateScenarioDescription() {
    const scenario = scenarios.find(item => (item.scenario_id || item.id) === $("scenario").value);
    $("scenario-description").textContent = scenario?.description || scenario?.task || "Controlled scenarios use simulated state; no real assets move.";
  }
  const bindingId = report => report.binding_id || report.manifest?.binding_id || report.manifest?.binding?.definition?.id || report.manifest?.binding?.id || "bundled";
  function updateBindingDescription() {
    const binding = bindings.find(item => item.id === $("binding").value);
    if ($("backend").value === "studio") {
      $("runtime-label").textContent = "Studio · GenVM · local fixtures";
      $("binding-description").textContent = "Requires your local Studio stack: gl-agent-lab studio build, then gl-agent-lab studio up. Contract execution and finalization are observed in Studio; the agent's scenario timeline remains scripted. Each run can take a few minutes.";
      return;
    }
    $("runtime-label").textContent = binding ? "Container GLSim · custom contract" : "GLSim · bundled contract";
    $("binding-description").textContent = binding
      ? `${binding.id} · method ${binding.method} · source ${binding.source_sha256.slice(0, 12)}. Runs use the imported source snapshot in an isolated Docker worker. Build the worker with gl-agent-lab worker build before testing.`
      : "Uses the bundled approval contract. To test your own contract, run gl-agent-lab import-binding with its YAML definition, then gl-agent-lab worker build. Refresh this page after restarting the service.";
  }
  function renderBindings() {
    const previous = $("binding").value;
    $("binding").replaceChildren(new Option("Bundled approval contract", ""));
    for (const binding of bindings) $("binding").append(new Option(`${binding.title} · ${binding.id}`, binding.id));
    if (bindings.some(binding => binding.id === previous)) $("binding").value = previous;
    updateBindingDescription();
  }
  async function connect() {
    [scenarios, bindings] = await Promise.all([json("/v1/scenarios"), json("/v1/bindings")]);
    $("scenario").replaceChildren();
    for (const scenario of scenarios) {
      const option = text("option", scenario.name || scenario.title || scenario.scenario_id || scenario.id);
      option.value = scenario.scenario_id || scenario.id;
      $("scenario").append(option);
    }
    updateScenarioDescription();
    renderBindings();
    sessionStorage.setItem(storageKey, token);
    $("auth").hidden = true; $("workspace").hidden = false; $("disconnect").hidden = false;
    $("token").value = "";
    await refresh();
  }
  function renderRuns() {
    $("stat-total").textContent = runs.length;
    $("stat-completed").textContent = runs.filter(run => run.status === "completed").length;
    $("stat-failed").textContent = runs.filter(run => run.verdict === "fail" || attentionStatuses.has(run.status)).length;
    $("stat-active").textContent = runs.filter(run => activeStatuses.has(run.status)).length;
    $("history-count").textContent = runs.length;
    const filter = $("filter").value;
    const visible = runs.filter(run => filter === "all" || (filter === "active" ? activeStatuses.has(run.status) : filter === "attention" ? run.verdict === "fail" || attentionStatuses.has(run.status) : run.status === filter));
    $("runs").replaceChildren();
    $("empty").hidden = runs.length > 0;
    for (const run of visible) {
      const row = document.createElement("tr");
      if (run.run_id === selected) row.className = "selected";
      const name = document.createElement("td");
      name.append(text("div", scenarioName(run), "run-name"), text("div", run.run_id, "run-id"),
        text("div", `${run.binding_id || "bundled"} · ${run.backend || "unknown backend"}`, "run-id"));
      row.append(name, text("td", run.agent || "external"));
      const status = document.createElement("td"); status.append(pill(run.status)); row.append(status);
      const result = document.createElement("td"); result.append(pill(run.verdict)); row.append(result);
      const date = run.created_at ? new Date(typeof run.created_at === "number" ? run.created_at * 1000 : run.created_at) : null;
      row.append(text("td", date && !isNaN(date) ? date.toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "—"));
      const action = document.createElement("td"), view = text("button", "Inspect →", "table-link");
      view.addEventListener("click", () => selectRun(run.run_id).catch(showError)); action.append(view); row.append(action);
      $("runs").append(row);
    }
    const prior = $("compare").value;
    $("compare").replaceChildren(new Option("Choose another run", ""));
    for (const run of runs.filter(run => run.run_id !== selected)) $("compare").append(new Option(`${scenarioName(run)} · ${run.run_id.slice(0, 8)}`, run.run_id));
    if ([...$("compare").options].some(option => option.value === prior)) $("compare").value = prior;
  }
  async function refresh() {
    if (refreshing || !token) return;
    refreshing = true;
    try {
      runs = await json("/v1/runs"); renderRuns();
      if (selected) await loadDetail(selected);
    } finally { refreshing = false; }
  }
  async function selectRun(runId) {
    selected = runId;
    $("compare").value = ""; $("comparison").hidden = true;
    renderRuns(); await loadDetail(runId);
    $("detail").scrollIntoView({behavior:"smooth", block:"start"});
  }
  async function loadDetail(runId) {
    const report = await json(`/v1/runs/${encodeURIComponent(runId)}/report`);
    if (selected !== runId) return;
    currentReport = report;
    $("detail").hidden = false;
    $("detail-title").textContent = scenarioName(report);
    $("detail-meta").textContent = `${runId} · ${report.agent || "external"} · ${report.status} · ${report.manifest?.backend || report.backend || "unknown backend"} · contract ${bindingId(report)}`;
    $("cancel").hidden = !activeStatuses.has(report.status);
    $("grades").replaceChildren();
    for (const [name, grade] of Object.entries(report.grades || {})) {
      const card = text("div", "", "grade");
      card.append(text("h3", name), pill(grade.status), text("p", grade.detail || "Waiting for evaluation")); $("grades").append(card);
    }
    $("findings").replaceChildren();
    for (const finding of report.findings || []) $("findings").append(text("div", typeof finding === "string" ? finding : finding.detail || finding.message || JSON.stringify(finding), "finding"));
    $("timeline").replaceChildren();
    for (const [index, event] of (report.events || []).entries()) {
      const item = document.createElement("li"), title = text("div", event.type || event.kind || event.event || "Recorded event", "event-heading");
      title.append(text("small", `#${event.sequence ?? event.seq ?? index + 1}${event.tick === undefined ? "" : ` · tick ${event.tick}`}`));
      const details = document.createElement("details");
      details.append(text("summary", "Inspect evidence"), text("pre", JSON.stringify(event, null, 2)));
      item.append(title, details); $("timeline").append(item);
    }
    if (!report.events?.length) $("timeline").append(text("li", "The environment is preparing. Events will appear here."));
    $("manifest").textContent = JSON.stringify(report.manifest || {}, null, 2);
  }
  $("auth-form").addEventListener("submit", async event => { event.preventDefault(); clearError(); token = $("token").value.trim(); try { await connect(); } catch(error) { showError(error); } });
  $("disconnect").addEventListener("click", () => { sessionStorage.removeItem(storageKey); token = ""; location.reload(); });
  $("scenario").addEventListener("change", updateScenarioDescription);
  $("binding").addEventListener("change", updateBindingDescription);
  $("backend").addEventListener("change", updateBindingDescription);
  $("filter").addEventListener("change", renderRuns);
  $("refresh").addEventListener("click", async () => {
    clearError();
    try { bindings = await json("/v1/bindings"); renderBindings(); await refresh(); }
    catch (error) { showError(error); }
  });
  $("launch-form").addEventListener("submit", async event => {
    event.preventDefault(); clearError(); $("launch-button").disabled = true;
    try {
      const chosenBinding = $("binding").value;
      const run = await json("/v1/runs", "POST", {scenario_id: $("scenario").value, agent: $("agent").value,
        backend: $("backend").value === "studio" ? "studio" : chosenBinding ? "container-glsim" : "glsim", ...(chosenBinding ? {binding_id: chosenBinding} : {})});
      $("connection").hidden = $("agent").value !== "external";
      $("run-credential").value = $("agent").value === "external" ? `LAB_URL=${location.origin}\nLAB_RUN_ID=${run.run_id}\nLAB_TOKEN=${run.agent_token}` : "";
      selected = run.run_id; await refresh();
    } catch(error) { showError(error); } finally { $("launch-button").disabled = false; }
  });
  $("copy-credential").addEventListener("click", async () => { try { await navigator.clipboard.writeText($("run-credential").value); $("copy-credential").textContent = "Copied"; } catch { $("run-credential").select(); showError("Copy is unavailable in this browser; the configuration is selected for manual copying."); } });
  $("cancel").addEventListener("click", async () => { try { await json(`/v1/runs/${encodeURIComponent(selected)}/cancel`, "POST"); await refresh(); } catch(error) { showError(error); } });
  document.querySelectorAll("[data-format]").forEach(button => button.addEventListener("click", async () => {
    try {
      const format = button.dataset.format;
      const response = await api(`/v1/runs/${encodeURIComponent(selected)}/report?format=${format}`);
      const url = URL.createObjectURL(await response.blob()), anchor = document.createElement("a");
      anchor.href = url; anchor.download = `lab-${selected}.${format === "junit" ? "xml" : format}`;
      anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch(error) { showError(error); }
  }));
  $("compare").addEventListener("change", async () => {
    $("comparison").hidden = !$("compare").value;
    if (!$("compare").value || !currentReport) return;
    try {
      const baseline = await json(`/v1/runs/${encodeURIComponent($("compare").value)}/report`);
      const table = document.createElement("table"), head = document.createElement("tr");
      head.append(text("th", "Dimension"), text("th", "Selected run"), text("th", "Comparison run")); table.append(head);
      for (const [name, grade] of Object.entries(currentReport.grades || {})) {
        const row = document.createElement("tr"); row.append(text("td", name), text("td", grade.status), text("td", baseline.grades?.[name]?.status || "unavailable")); table.append(row);
      }
      $("comparison").replaceChildren(table);
    } catch(error) { showError(error); }
  });
  if (token) connect().catch(showError);
  setInterval(() => { if (token && !document.hidden) refresh().catch(showError); }, 3000);
})();
