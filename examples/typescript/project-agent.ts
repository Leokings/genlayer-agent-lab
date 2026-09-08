/** Scripted prediction reference through four run-scoped tools. Node.js 24. */
import { pathToFileURL } from "node:url";
import { LabClient, LabError, stringifyProjectJson } from "./client.ts";

type ObjectValue = Record<string, unknown>;
type Operation = ObjectValue & { operation: string; idempotency_key: string; status: string };
type Observation = ObjectValue & {
  run_id: string; profile: "project"; status: string;
  context: ObjectValue; policy: ObjectValue; state: ObjectValue;
  operations: Operation[]; transactions: Record<string, ObjectValue>;
};
type ScopedClient = Pick<LabClient,
  "workflowObserve" | "workflowInvoke" | "workflowAppeal" | "workflowFinish">;
export type ProjectAction =
  | { kind: "invoke"; operation: string; arguments: ObjectValue; idempotency_key: string; expected_decision_id?: string }
  | { kind: "appeal"; idempotency_key: string; expected_decision_id: string }
  | { kind: "finish" }
  | { kind: "stop"; status: string };
export type ProjectAgentOptions = {
  unsafe?: boolean; timeoutSeconds?: number; pollIntervalSeconds?: number;
};
const TERMINAL = new Set(["completed", "inconclusive", "cancelled"]);
const RUN_STATUSES = new Set(["preparing", "running", "recovering", "closing", ...TERMINAL]);
const OPERATION_STATUSES = new Set([
  "queued", "prepared", "submitting", "uncertain", "submitted", "completed", "failed", "rejected", "cancelled",
]);
const KEY = {
  evidence: "project-reference:evidence", resolve: "project-reference:resolve",
  inspect: "project-reference:appeal-quote", appeal: "project-reference:appeal",
  record: "project-reference:record", unsafe: "project-reference:unsafe",
};

function object(value: unknown): value is ObjectValue {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function observation(value: unknown, runId?: string): Observation {
  if (!object(value) || typeof value.run_id !== "string" || (runId !== undefined && value.run_id !== runId)
    || value.profile !== "project" || typeof value.status !== "string" || !RUN_STATUSES.has(value.status)
    || !object(value.context) || !object(value.state) || !object(value.policy)
    || typeof value.policy.allow_appeal !== "boolean" || !Array.isArray(value.operations)
    || !object(value.transactions)) throw new Error("Invalid project observation");
  if (value.operations.some(item => !object(item) || typeof item.operation !== "string"
    || typeof item.idempotency_key !== "string" || typeof item.status !== "string"
    || !OPERATION_STATUSES.has(item.status))) throw new Error("Invalid project operation journal");
  if (Object.values(value.transactions).some(item => !object(item))) throw new Error("Invalid transaction journal");
  return value as Observation;
}

/** Chooses from public state only; no scenario file, expected answer or admin report. */
export function projectAgentStep(value: unknown, unsafe = false): ProjectAction | null {
  const current = observation(value);
  if (TERMINAL.has(current.status)) return { kind: "stop", status: current.status };
  if (current.status !== "running") return null;
  const existing = (key: string) => current.operations.find(item => item.idempotency_key === key);
  const invoke = (operation: string, args: ObjectValue, key: string, decision?: string): ProjectAction => ({
    kind: "invoke", operation, arguments: args, idempotency_key: key,
    ...(decision === undefined ? {} : { expected_decision_id: decision }),
  });
  if (current.operations.some(item => ["rejected", "failed", "cancelled"].includes(item.status))) {
    return { kind: "finish" };
  }
  const evidence = existing(KEY.evidence);
  if (!evidence) return invoke("read_evidence", { id: "settlement_record" }, KEY.evidence);
  if (evidence.status !== "completed") return null;
  if (!object(evidence.result) || typeof evidence.result.content !== "string"
    || !evidence.result.content.includes("reports yes")) {
    throw new Error("Reference agent cannot interpret the supplied evidence");
  }
  const resolve = existing(KEY.resolve);
  if (!resolve) {
    if (typeof current.context.evidence !== "string") throw new Error("Missing public evidence input");
    return invoke("resolve", { evidence: current.context.evidence }, KEY.resolve);
  }
  if (unsafe && !existing(KEY.unsafe)) {
    return invoke("record", { expected_revision: 1, outcome: "yes" }, KEY.unsafe);
  }
  const decision = Object.values(current.transactions).find(item => item.operation === "resolve");
  if (!decision || decision.execution_success !== true) return null;
  if (!object(decision.result) || typeof decision.result.outcome !== "string"
    || !["yes", "no", "void"].includes(decision.result.outcome)
    || typeof decision.decision_id !== "string" || !decision.decision_id) {
    throw new Error("Invalid executed decision");
  }
  const appeal = existing(KEY.appeal);
  if (decision.result.outcome !== "yes" && current.policy.allow_appeal && !appeal) {
    if (decision.appeal_eligible !== true) {
      return ["FINALIZED", "CANCELED"].includes(String(decision.status)) ? { kind: "finish" } : null;
    }
    const inspected = existing(KEY.inspect);
    if (!inspected) return invoke("inspect_appeal", { decision_id: decision.decision_id }, KEY.inspect);
    if (inspected.status !== "completed") return null;
    return { kind: "appeal", idempotency_key: KEY.appeal, expected_decision_id: decision.decision_id };
  }
  if (appeal && appeal.status !== "completed") return null;
  if (decision.status !== "FINALIZED") return null;
  const state = current.state.oracle_state;
  if (!object(state) || !Number.isSafeInteger(state.revision) || (state.revision as number) < 1) return null;
  // A read arriving from an earlier revision must not drive a final write.
  if (state.outcome !== decision.result.outcome || state.revision !== decision.result.revision) return null;
  const recorded = existing(KEY.record);
  if (!recorded) {
    return invoke("record", { expected_revision: state.revision, outcome: state.outcome },
      KEY.record, decision.decision_id);
  }
  return recorded.status === "completed" ? { kind: "finish" } : null;
}

function retryable(error: unknown): boolean {
  if (error instanceof LabError) return [408, 429].includes(error.statusCode)
    || error.statusCode >= 500 || (error.statusCode >= 200 && error.statusCode < 300);
  return error instanceof TypeError || (error instanceof Error && ["AbortError", "TimeoutError"].includes(error.name));
}

function duration(value: number, name: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || value > 86_400) {
    throw new RangeError(`${name} must be positive, finite and at most 86400 seconds`);
  }
  return value * 1000;
}

/** The same action and idempotency key survive a lost HTTP response. */
export async function runProjectAgent(client: ScopedClient, runId: string, options: ProjectAgentOptions = {}) {
  if (!runId || runId.includes("/") || [".", ".."].includes(runId)) throw new Error("Invalid project run ID");
  const deadline = performance.now() + duration(options.timeoutSeconds ?? 900, "timeoutSeconds");
  const interval = duration(options.pollIntervalSeconds ?? 0.5, "pollIntervalSeconds");
  let pending: ProjectAction | null = null;
  let finishing = false;
  const result = (status: string) => ({ run_id: runId, status, driver: "scripted_prediction_typescript" });
  const request = async <T>(call: () => Promise<T>): Promise<T> => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([call(), new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("Agent deadline exceeded")), Math.max(1, deadline - performance.now()));
      })]);
    } finally { clearTimeout(timer); }
  };
  while (performance.now() < deadline) {
    try {
      if (pending) {
        const action = pending;
        if (action.kind === "invoke") {
          await request(() => client.workflowInvoke(runId, action.operation, action.arguments,
            action.idempotency_key, action.expected_decision_id));
        } else if (action.kind === "appeal") {
          await request(() => client.workflowAppeal(runId, action.idempotency_key, action.expected_decision_id));
        } else if (action.kind === "finish") {
          await request(() => client.workflowFinish(runId));
          finishing = true;
        }
        pending = null;
      }
      const current = observation(await request(() => client.workflowObserve(runId)), runId);
      if (TERMINAL.has(current.status)) return result(current.status);
      if (!finishing) pending = projectAgentStep(current, options.unsafe ?? false);
    } catch (error) {
      if (performance.now() >= deadline) return result("agent_deadline_exceeded");
      if (!retryable(error)) return result("client_error");
    }
    await new Promise(resolve => setTimeout(resolve, Math.max(0, Math.min(interval, deadline - performance.now()))));
  }
  return result("agent_deadline_exceeded");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [mode = "safe", timeout = "900", ...extra] = process.argv.slice(2);
  if (!["safe", "unsafe"].includes(mode) || extra.length) throw new Error("Usage: project-agent.ts [safe|unsafe] [timeout-seconds]");
  const runId = process.env.LAB_RUN_ID;
  if (!runId) throw new Error("Set LAB_RUN_ID and its run-scoped LAB_TOKEN");
  const client = new LabClient(process.env.LAB_URL ?? "http://127.0.0.1:8765", process.env.LAB_TOKEN ?? "");
  console.log(stringifyProjectJson(await runProjectAgent(client, runId, {
    unsafe: mode === "unsafe", timeoutSeconds: Number(timeout),
  }), null, 2));
}
