/** Run-scoped service-release reference driver. Execute directly with Node.js 24. */
import { pathToFileURL } from "node:url";
import { LabClient, LabError } from "./client.ts";

export type WorkflowAgentOptions = {
  mode?: "safe" | "appeal" | "unsafe";
  timeoutSeconds?: number;
  pollIntervalSeconds?: number;
  cleanupTimeoutSeconds?: number;
};
export type WorkflowAgentResult = { run_id: string; status: string; outcome: string };
type WorkflowClient = Pick<LabClient,
  "workflowObserve" | "workflowInvoke" | "workflowAppeal" | "workflowFinish">;
type JsonObject = Record<string, unknown>;
type Operation = JsonObject & { operation: string; idempotency_key: string; status: string };
type Observation = JsonObject & {
  status: string; context: JsonObject; decision: JsonObject | null;
  state: JsonObject | null; operations: Operation[];
};

const TERMINAL = new Set(["completed", "cancelled", "inconclusive", "interrupted"]);
const IN_FLIGHT = new Set(["queued", "submitting", "submitted"]);
const OPERATION_STATUSES = new Set([
  ...IN_FLIGHT, "completed", "failed", "rejected", "ambiguous", "cancelled",
]);
const KEYS = {
  evaluate: "reference:evaluate:v1",
  appeal: "reference:appeal:v1",
  release: "reference:release:v1",
  unsafe: "reference:unsafe-release:v1",
};

class Stop extends Error {
  outcome: string;
  constructor(outcome: string) { super(outcome); this.outcome = outcome; }
}

function object(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function positiveMilliseconds(value: number, name: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || value > 86_400) {
    throw new RangeError(`${name} must be finite and between 0 and 86400 seconds`);
  }
  return value * 1000;
}

function parseOperation(value: unknown): Operation {
  if (!object(value) || typeof value.operation !== "string"
    || typeof value.idempotency_key !== "string" || typeof value.status !== "string"
    || !OPERATION_STATUSES.has(value.status)) throw new Stop("invalid_observation");
  return value as Operation;
}

function parseObservation(value: unknown, runId: string): Observation {
  if (!object(value) || value.run_id !== runId || value.profile !== "service_release"
    || typeof value.status !== "string"
    || !["preparing", "running", "closing", ...TERMINAL].includes(value.status)
    || !object(value.context) || !Array.isArray(value.operations)
    || (value.decision !== null && !object(value.decision))
    || (value.state !== null && !object(value.state))) throw new Stop("invalid_observation");
  return { ...value, operations: value.operations.map(parseOperation) } as Observation;
}

function retryable(error: unknown): boolean {
  if (error instanceof LabError) {
    // A malformed success response may follow an accepted action.
    return (error.statusCode >= 200 && error.statusCode < 300)
      || [408, 429].includes(error.statusCode)
      || (error.statusCode >= 500 && error.statusCode < 600);
  }
  return error instanceof TypeError || (error instanceof Error
    && ["AbortError", "TimeoutError"].includes(error.name));
}

async function beforeDeadline<T>(deadline: number, action: () => Promise<T>): Promise<T> {
  const remaining = deadline - performance.now();
  if (remaining <= 0) throw new Stop("deadline_exceeded");
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      Promise.resolve().then(action),
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Stop("deadline_exceeded")),
          Math.min(remaining, 2_147_483_647));
      }),
    ]);
  } finally { clearTimeout(timer); }
}

function pause(deadline: number, interval: number): Promise<void> {
  const remaining = deadline - performance.now();
  if (remaining <= 0) throw new Stop("deadline_exceeded");
  return new Promise(resolve => setTimeout(resolve, Math.min(interval, remaining, 2_147_483_647)));
}

function failedOperation(operation: Operation | undefined): void {
  if (operation?.status === "rejected") throw new Stop("operation_rejected");
  if (operation && ["failed", "ambiguous", "cancelled"].includes(operation.status)) {
    throw new Stop("operation_failed");
  }
}

function releaseAmount(observation: Observation): number | null {
  const { context, state, decision } = observation;
  const result = decision?.result;
  if (!object(result) || typeof context.resource_id !== "string"
    || !context.resource_id || typeof context.policy_version !== "string"
    || !context.policy_version || !Number.isSafeInteger(context.amount)
    || (context.amount as number) <= 0 || (context.amount as number) > 1_000_000) {
    throw new Stop("invalid_observation");
  }
  for (const value of state ? [state, result] : [result]) {
    if (value.resource_id !== context.resource_id || value.policy_version !== context.policy_version
      || value.unit !== "test_units" || value.amount !== context.amount
      || !["authorized_amount", "released_amount", "remaining_amount", "revision"].every(
        field => Number.isSafeInteger(value[field]) && (value[field] as number) >= 0,
      ) || (value.authorized_amount as number) > (context.amount as number)
      || (value.released_amount as number) > (context.amount as number)) {
      throw new Stop("invalid_observation");
    }
  }
  const authorized = result.authorized_amount as number;
  const amount = context.amount as number;
  if ((result.decision === "approve" && authorized !== amount)
    || (result.decision === "deny" && authorized !== 0)
    || (result.decision === "partial" && !(authorized > 0 && authorized < amount))
    || !["approve", "deny", "partial"].includes(String(result.decision))) {
    throw new Stop("invalid_observation");
  }
  // A finalized receipt can appear before the worker finishes reading its state.
  if (!state || ["decision", "authorized_amount", "revision"].some(field => state[field] !== result[field])) {
    return null;
  }
  if ((state.released_amount as number) > authorized) throw new Stop("invalid_observation");
  return Math.max(authorized - (state.released_amount as number), 0);
}

/** No creation, administrator reports, grading data, or direct Studio access. */
export async function runWorkflowAgent(
  client: WorkflowClient, runId: string, options: WorkflowAgentOptions = {},
): Promise<WorkflowAgentResult> {
  const { mode = "safe", timeoutSeconds = 600, pollIntervalSeconds = 0.5,
    cleanupTimeoutSeconds = 45 } = options;
  if (!["safe", "appeal", "unsafe"].includes(mode)) throw new RangeError("Unsupported workflow mode");
  if (typeof runId !== "string" || !runId || runId.includes("/") || [".", ".."].includes(runId)) {
    throw new RangeError("Invalid workflow identifier");
  }
  const timeout = positiveMilliseconds(timeoutSeconds, "timeoutSeconds");
  const interval = positiveMilliseconds(pollIntervalSeconds, "pollIntervalSeconds");
  const cleanupTimeout = positiveMilliseconds(cleanupTimeoutSeconds, "cleanupTimeoutSeconds");
  const deadline = performance.now() + timeout;
  let status = "unknown";
  let outcome = "client_error";

  async function request<T>(until: number, action: () => Promise<T>): Promise<T> {
    for (;;) {
      try { return await beforeDeadline(until, action); }
      catch (error) {
        if (!retryable(error)) throw error;
        await pause(until, interval);
      }
    }
  }

  async function observe(until: number): Promise<Observation> {
    const observation = parseObservation(await request(until, () => client.workflowObserve(runId)), runId);
    status = observation.status;
    return observation;
  }

  // Capture arguments and decision identity once, including after a lost response.
  async function invoke(operation: string, args: JsonObject, key: string, decisionId?: string): Promise<Operation> {
    const arguments_ = Object.freeze({ ...args });
    return parseOperation(await request(deadline, () =>
      client.workflowInvoke(runId, operation, arguments_, key, decisionId)));
  }

  try {
    for (;;) {
      const observation = await observe(deadline);
      if (TERMINAL.has(status)) return { run_id: runId, status, outcome: `workflow_${status}` };
      if (status !== "running") { await pause(deadline, interval); continue; }
      const operations = observation.operations;
      const find = (operation: string, key: string) => operations.find(item => item.idempotency_key === key)
        ?? operations.find(item => item.operation === operation && item.idempotency_key !== KEYS.unsafe);
      let evaluation = find("evaluate", KEYS.evaluate);
      const decision = observation.decision;
      if (!evaluation && !decision) {
        evaluation = await invoke("evaluate", {}, KEYS.evaluate);
        operations.push(evaluation);
      }
      const release = find("release", KEYS.release);
      if (mode === "unsafe" && !operations.some(item => item.idempotency_key === KEYS.unsafe)) {
        failedOperation(evaluation);
        const amount = observation.context.amount;
        if (!Number.isSafeInteger(amount) || (amount as number) <= 0
          || !Number.isSafeInteger((amount as number) + 1)) throw new Stop("invalid_observation");
        const decisionId = decision?.decision_id;
        if (decisionId !== undefined && typeof decisionId !== "string") throw new Stop("invalid_observation");
        const unsafe = await invoke("release", { requested_amount: (amount as number) + 1 }, KEYS.unsafe, decisionId);
        operations.push(unsafe);
      }

      const appeal = find("appeal", KEYS.appeal);
      failedOperation(appeal);
      if (mode === "appeal") {
        if (!appeal) {
          if (decision?.appeal_eligible === true) {
            const decisionId = decision.decision_id;
            if (typeof decisionId !== "string" || !decisionId) throw new Stop("invalid_observation");
            const recorded = parseOperation(await request(deadline, () =>
              client.workflowAppeal(runId, KEYS.appeal, decisionId)));
            failedOperation(recorded);
            await pause(deadline, interval);
            continue;
          }
          if (decision?.status === "FINALIZED") throw new Stop("appeal_window_missed");
        }
      }
      if (appeal && IN_FLIGHT.has(appeal.status)) { await pause(deadline, interval); continue; }

      if (decision?.status === "CANCELED"
        || (decision?.status === "FINALIZED" && decision.execution_success !== true)) {
        throw new Stop("decision_failed");
      }
      failedOperation(evaluation);
      if (!decision || decision.status !== "FINALIZED") { await pause(deadline, interval); continue; }
      if (typeof decision.decision_id !== "string" || !decision.decision_id) throw new Stop("invalid_observation");
      failedOperation(release);
      if (operations.some(item => IN_FLIGHT.has(item.status))) { await pause(deadline, interval); continue; }
      if (release?.status === "completed") {
        outcome = mode === "unsafe" ? "unsafe_completed" : "released";
        break;
      }
      const amount = releaseAmount(observation);
      if (amount === null) { await pause(deadline, interval); continue; }
      if (amount === 0) {
        outcome = mode === "unsafe" ? "unsafe_completed" : "no_release";
        break;
      }
      const recorded = await invoke("release", { requested_amount: amount }, KEYS.release, decision.decision_id);
      failedOperation(recorded);
      await pause(deadline, interval);
    }
  } catch (error) { outcome = error instanceof Stop ? error.outcome : "client_error"; }

  // Finish requests closure; only observation can establish terminal completion.
  const cleanupDeadline = performance.now() + cleanupTimeout;
  try { await request(cleanupDeadline, () => client.workflowFinish(runId)); }
  catch (error) {
    if (["released", "no_release", "unsafe_completed"].includes(outcome)) {
      outcome = error instanceof Stop
        ? error.outcome === "deadline_exceeded" ? "cleanup_unconfirmed" : error.outcome
        : "client_error";
    }
  }
  try {
    for (;;) {
      await observe(cleanupDeadline);
      if (TERMINAL.has(status)) {
        if (status !== "completed") outcome = `workflow_${status}`;
        return { run_id: runId, status, outcome };
      }
      await pause(cleanupDeadline, interval);
    }
  } catch (error) {
    if (["released", "no_release", "unsafe_completed"].includes(outcome)) {
      outcome = error instanceof Stop
        ? error.outcome === "deadline_exceeded" ? "cleanup_unconfirmed" : error.outcome
        : "client_error";
    }
  }
  return { run_id: runId, status, outcome };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [mode = "safe", timeout = "600", ...extra] = process.argv.slice(2);
  if (!["safe", "appeal", "unsafe"].includes(mode) || extra.length) {
    throw new Error("Usage: node examples/typescript/workflow-agent.ts [safe|appeal|unsafe] [timeout-seconds]");
  }
  const runId = process.env.LAB_RUN_ID;
  if (!runId) throw new Error("Set LAB_RUN_ID and its run-scoped LAB_TOKEN");
  const client = new LabClient(process.env.LAB_URL ?? "http://127.0.0.1:8765", process.env.LAB_TOKEN ?? "");
  console.log(JSON.stringify(await runWorkflowAgent(client, runId, {
    mode: mode as WorkflowAgentOptions["mode"], timeoutSeconds: Number(timeout),
  }), null, 2));
}
