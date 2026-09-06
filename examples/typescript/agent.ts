/** Scripted reference behavior. No agent framework or engine imports are used. */
import { LabClient, LabError } from "./client.ts";
import type { Action, Observation, Report } from "./client.ts";

export async function runAgent(client: LabClient, runId: string, timeoutMs = 180_000): Promise<Report> {
  const deadline = Date.now() + timeoutMs;
  let observation: Observation | undefined;
  while (Date.now() < deadline) {
    try {
      observation = await client.observe(runId);
      if (observation.status === "running") break;
    } catch (error) {
      if (!(error instanceof LabError) || error.statusCode !== 409) throw error;
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  if (!observation || observation.status !== "running") throw new Error("Test did not become ready before the deadline");
  const task = observation.task;
  await client.requestDecision(runId, `${runId}:decision`);
  for (let attempt = 0; attempt < 20; attempt++) {
    if (Date.now() >= deadline) throw new Error("Agent exceeded its wall-clock limit");
    await client.observe(runId);
    const decision = await client.readDecision(runId);
    if (!decision || ["pending", "provisional"].includes(decision.status)) continue;
    if (decision.status === "final" && decision.execution_result === "success" && decision.verdict === "approve" && decision.resource_id === task.resource_id && decision.policy_version === task.policy_version && Number.isInteger(decision.revision)) {
      const action: Action = {
        operation: task.operation, resource_id: task.resource_id, policy_version: task.policy_version,
        decision_id: decision.decision_id, revision: decision.revision,
        amount: task.amount, idempotency_key: `${runId}:action`,
      };
      const result = await client.act(runId, action);
      if (result.status === "unknown") await client.act(runId, action);
    }
    break;
  }
  return client.finish(runId);
}

const baseUrl = process.env.LAB_URL ?? "http://127.0.0.1:8765";
let token = process.env.LAB_TOKEN ?? "";
let runId = process.env.LAB_RUN_ID;
const scenarioArg = process.argv.indexOf("--scenario");
if (scenarioArg !== -1) {
  const scenario = process.argv[scenarioArg + 1];
  if (!scenario) throw new Error("--scenario requires an identifier from listScenarios()");
  const created = await new LabClient(baseUrl, token).createRun(scenario);
  token = created.agent_token;
  runId = created.run_id;
}
if (!runId) throw new Error("Set LAB_RUN_ID and run-scoped LAB_TOKEN, or supply --scenario and an admin token");
console.log(JSON.stringify(await runAgent(new LabClient(baseUrl, token), runId), null, 2));
