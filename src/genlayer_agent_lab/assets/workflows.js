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
  let token = sessionStorage.getItem(key) || "", selected = null, report = null, busy = false;
  const terminal = new Set(["completed", "cancelled", "interrupted", "inconclusive"]);
  const fail = error => { $("notice").textContent = String(error.message || error).replaceAll(token || "\0", "[redacted]"); $("notice").hidden = false; };
  async function request(path, method = "GET", body) {
    const response = await fetch(`/v1/workflows${path}`, {method, redirect:"error", signal:AbortSignal.timeout(30000), headers:{Authorization:`Bearer ${token}`, ...(body === undefined ? {} : {"Content-Type":"application/json"})}, body:body === undefined ? undefined : stringifyProjectJson(body)});
    const result = parseProjectJson(await response.text());
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
        const project = run.profile === "project";
        $("status").textContent = `${run.status} · local Studio · ${project ? "project workflow / local test balances" : "test units"}`;
        $("cancel").hidden = terminal.has(run.status);
        $("decision").textContent = stringifyProjectJson(project ? (run.transactions || {}) : (run.decision || "Waiting for an agent decision request"), null, 2);
        $("project-detail").hidden = !project;
        if (project) {
          $("contracts").textContent = stringifyProjectJson(run.contracts || {}, null, 2);
          $("accounting").textContent = stringifyProjectJson({account:run.account_address, reserved_deposits:run.fee_reserved, setup_deposits:evidence.setup_fee_reserved, final_balance:evidence.final_balance, unit:run.fee_unit, note:evidence.fee_accounting_note}, null, 2);
          $("recovery").textContent = stringifyProjectJson({cleanup:evidence.cleanup, scope:evidence.recovery_scope, journal:evidence.signer_persistence}, null, 2);
        }
        $("state").textContent = stringifyProjectJson(run.state || "Waiting for deployment", null, 2);
        $("operations").replaceChildren();
        for (const operation of run.operations || []) $("operations").append(node("pre", stringifyProjectJson(operation, null, 2)));
        $("evaluation").textContent = stringifyProjectJson({verification:evidence.verification, checks:evidence.checks ?? null, grades:evidence.grades ?? null, cleanup:evidence.cleanup ?? null, error_code:evidence.error_code ?? null}, null, 2);
        $("evidence").textContent = stringifyProjectJson(evidence, null, 2);
      }
    } finally { busy = false; }
  }
  async function connect() {
    await refresh(); sessionStorage.setItem(key, token); $("auth").hidden = true; $("workspace").hidden = false; $("token").value = "";
  }
  $("spec").value = stringifyProjectJson({schema_version:1, profile:"service_release", context:{resource_id:"service-001",policy_version:"v1",amount:100,evidence:"The service delivered forty of one hundred agreed test units."},initial_fixture:{decision:"partial",authorized_amount:40},expectations:{final_state:{decision:"partial",authorized_amount:40,released_amount:40,remaining_amount:60},required_actions:["evaluate","release"]},timeout_seconds:600}, null, 2);
  $("auth-form").addEventListener("submit", event => { event.preventDefault(); token = $("token").value.trim(); connect().catch(fail); });
  $("disconnect").addEventListener("click", () => { sessionStorage.removeItem(key); location.reload(); });
  $("load-spec").addEventListener("change", async event => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      if (file.size > 2400000) throw new Error("Scenario file exceeds the supported size.");
      const spec = parseProjectJson(await file.text());
      if (spec.integer_encoding === "lab-tagged-decimal-v1") delete spec.integer_encoding;
      if (spec.schema_version === 2 && spec.review?.status !== "approved") throw new Error("Review this project scenario with the project approve command before starting it.");
      $("spec").value = stringifyProjectJson(spec, null, 2);
    } catch (error) { fail(error); }
  });
  $("refresh").addEventListener("click", () => refresh().catch(fail));
  $("create-form").addEventListener("submit", async event => {
    event.preventDefault(); $("create").disabled = true; $("notice").hidden = true;
    try {
      const spec = parseProjectJson($("spec").value);
      if (spec.integer_encoding === "lab-tagged-decimal-v1") delete spec.integer_encoding;
      const result = await request("", "POST", {spec});
      selected = result.run_id;
      $("credential").value = `LAB_URL=${location.origin}\nLAB_MODE=workflow\nLAB_ROLE=agent\nLAB_RUN_ID=${result.run_id}\nLAB_TOKEN=${result.agent_token}`;
      $("connection").hidden = false; await refresh();
    } catch (error) { fail(error); } finally { $("create").disabled = false; }
  });
  $("cancel").addEventListener("click", async () => { try { await request(`/${encodeURIComponent(selected)}/cancel`, "POST"); await refresh(); } catch (error) { fail(error); } });
  $("download").addEventListener("click", () => {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([stringifyProjectJson(report, null, 2)], {type:"application/json"}));
    const link = node("a", "Report"); link.href = url; link.download = `workflow-${selected}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  setInterval(() => { if (token && !document.hidden && !$("workspace").hidden) refresh().catch(fail); }, 5000);
  if (token) connect().catch(fail);
})();
