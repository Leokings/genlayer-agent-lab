"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const key = "genlayer-agent-lab-admin-token";
  let token = sessionStorage.getItem(key) || "", selected = null, report = null, busy = false;
  const terminal = new Set(["completed", "cancelled", "interrupted", "inconclusive"]);
  const fail = error => { $("notice").textContent = String(error.message || error).replaceAll(token || "\0", "[redacted]"); $("notice").hidden = false; };
  async function request(path, method = "GET", body) {
    const response = await fetch(`/v1/workflows${path}`, {method, redirect:"error", signal:AbortSignal.timeout(30000), headers:{Authorization:`Bearer ${token}`, ...(body === undefined ? {} : {"Content-Type":"application/json"})}, body:body === undefined ? undefined : JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : `Request failed (${response.status})`);
    return result;
  }
  function node(tag, value) { const element = document.createElement(tag); element.textContent = value; return element; }
  async function refresh() {
    if (busy) return;
    busy = true;
    try {
      const runs = await request("");
      $("runs").replaceChildren();
      for (const run of runs) {
        const button = node("button", `${run.run_id} · ${run.status}`);
        button.className = "secondary";
        button.addEventListener("click", () => { selected = run.run_id; refresh().catch(fail); });
        $("runs").append(button);
      }
      if (!runs.length) $("runs").append(node("p", "No workflow runs yet."));
      if (selected) {
        const prefix = `/${encodeURIComponent(selected)}`;
        const [run, evidence] = await Promise.all([request(prefix), request(`${prefix}/report`)]);
        report = evidence;
        $("detail").hidden = false;
        $("title").textContent = selected;
        $("status").textContent = `${run.status} · local Studio · test units`;
        $("cancel").hidden = terminal.has(run.status);
        $("decision").textContent = JSON.stringify(run.decision || "Waiting for an agent decision request", null, 2);
        $("state").textContent = JSON.stringify(run.state || "Waiting for deployment", null, 2);
        $("operations").replaceChildren();
        for (const operation of run.operations || []) $("operations").append(node("pre", JSON.stringify(operation, null, 2)));
        $("evaluation").textContent = JSON.stringify({verification:evidence.verification, grades:evidence.grades, cleanup:evidence.cleanup, error_code:evidence.error_code}, null, 2);
        $("evidence").textContent = JSON.stringify(evidence, null, 2);
      }
    } finally { busy = false; }
  }
  async function connect() {
    await refresh(); sessionStorage.setItem(key, token); $("auth").hidden = true; $("workspace").hidden = false; $("token").value = "";
  }
  $("spec").value = JSON.stringify({schema_version:1, profile:"service_release", context:{resource_id:"service-001",policy_version:"v1",amount:100,evidence:"The service delivered forty of one hundred agreed test units."},initial_fixture:{decision:"partial",authorized_amount:40},expectations:{final_state:{decision:"partial",authorized_amount:40,released_amount:40,remaining_amount:60},required_actions:["evaluate","release"]},timeout_seconds:600}, null, 2);
  $("auth-form").addEventListener("submit", event => { event.preventDefault(); token = $("token").value.trim(); connect().catch(fail); });
  $("disconnect").addEventListener("click", () => { sessionStorage.removeItem(key); location.reload(); });
  $("refresh").addEventListener("click", () => refresh().catch(fail));
  $("create-form").addEventListener("submit", async event => {
    event.preventDefault(); $("create").disabled = true; $("notice").hidden = true;
    try {
      const result = await request("", "POST", {spec:JSON.parse($("spec").value)});
      selected = result.run_id;
      $("credential").value = `LAB_URL=${location.origin}\nLAB_MODE=workflow\nLAB_ROLE=agent\nLAB_RUN_ID=${result.run_id}\nLAB_TOKEN=${result.agent_token}`;
      $("connection").hidden = false; await refresh();
    } catch (error) { fail(error); } finally { $("create").disabled = false; }
  });
  $("cancel").addEventListener("click", async () => { try { await request(`/${encodeURIComponent(selected)}/cancel`, "POST"); await refresh(); } catch (error) { fail(error); } });
  $("download").addEventListener("click", () => {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type:"application/json"}));
    const link = node("a", "Report"); link.href = url; link.download = `workflow-${selected}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  setInterval(() => { if (token && !document.hidden && !$("workspace").hidden) refresh().catch(fail); }, 5000);
  if (token) connect().catch(fail);
})();
