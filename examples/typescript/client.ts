/** Public HTTP client. Node.js 24 executes this TypeScript without a build step. */
import { isIP } from "node:net";

export type Decision = {
  decision_id: string;
  verdict: "approve" | "deny";
  status: "pending" | "provisional" | "final";
  execution_result: "success" | "error";
  resource_id: string;
  policy_version: string;
  revision: number;
};
export type Action = {
  operation: string; resource_id: string; policy_version: string;
  decision_id: string; revision: number; amount: number; idempotency_key: string;
};
export type Observation = {
  run_id: string; status: string; tick: number;
  task: { description: string; operation: string; resource_id: string; policy_version: string; amount: number };
  world: Record<string, unknown>; decision: Decision | null;
};
export type Report = {
  run_id: string; status: string; verdict: string;
  grades: Record<string, {status: string; detail: string}>;
  [key: string]: unknown;
};
export type Backend = "glsim" | "fixture" | "container-glsim" | "studio";
export type ContractBinding = {
  id: string; title: string; method: string; source_sha256: string; binding_sha256: string;
};

export class LabError extends Error {
  statusCode: number;
  constructor(message: string, statusCode: number) {
    super(message);
    this.name = "LabError";
    this.statusCode = statusCode;
  }
}

export class LabClient {
  private baseUrl: string;
  private token: string;
  constructor(baseUrl: string, token: string) {
    const url = new URL(baseUrl);
    const host = url.hostname.replace(/^\[|\]$/g, "");
    const loopback = host === "localhost" || host === "::1" || (isIP(host) === 4 && host.startsWith("127."));
    if (!loopback || !["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== "/") {
      throw new Error("LAB_URL must be a loopback HTTP(S) origin");
    }
    if (!token || token.length > 256 || /[^\x21-\x7e]/.test(token)) throw new Error("An ASCII bearer token of 1 to 256 characters without whitespace is required");
    this.baseUrl = url.origin;
    this.token = token;
  }

  private path(runId: string): string {
    if (!runId || runId.includes("/") || [".", ".."].includes(runId)) throw new Error("Invalid run identifier");
    return `/v1/runs/${encodeURIComponent(runId)}`;
  }
  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method, redirect: "error", signal: AbortSignal.timeout(30_000),
      headers: {Authorization: `Bearer ${this.token}`, ...(body === undefined ? {} : {"Content-Type": "application/json"})},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let content: any;
    try { content = await response.json(); }
    catch { throw new LabError("Lab returned an invalid JSON response", response.status); }
    if (!response.ok) {
      const detail = String(content.detail ?? "Request rejected").replaceAll(this.token, "[redacted]").slice(0, 1000);
      throw new LabError(`Lab HTTP ${response.status}: ${detail}`, response.status);
    }
    return content as T;
  }
  listScenarios(): Promise<Record<string, unknown>[]> { return this.request("GET", "/v1/scenarios"); }
  listBindings(): Promise<ContractBinding[]> { return this.request("GET", "/v1/bindings"); }
  listRuns(): Promise<Record<string, unknown>[]> { return this.request("GET", "/v1/runs"); }
  createRun(scenarioId: string, agent = "external", backend: Backend = "glsim", bindingId?: string): Promise<{run_id: string; agent_token: string; status: string}> {
    if (!["glsim", "fixture", "container-glsim", "studio"].includes(backend)) throw new Error("Unsupported runtime backend");
    if ((bindingId !== undefined && !["container-glsim", "studio"].includes(backend)) || (backend === "container-glsim" && !bindingId)) throw new Error("Bindings require container-glsim or studio; container-glsim requires a binding");
    return this.request("POST", "/v1/runs", {scenario_id: scenarioId, agent, backend, ...(bindingId === undefined ? {} : {binding_id: bindingId})});
  }
  getRun(runId: string): Promise<Record<string, unknown>> { return this.request("GET", this.path(runId)); }
  cancelRun(runId: string): Promise<Record<string, unknown>> { return this.request("POST", `${this.path(runId)}/cancel`); }
  report(runId: string): Promise<Report> { return this.request("GET", `${this.path(runId)}/report`); }
  observe(runId: string): Promise<Observation> { return this.request("POST", `${this.path(runId)}/observe`); }
  requestDecision(runId: string, key: string): Promise<Decision> { return this.request("POST", `${this.path(runId)}/decision`, {idempotency_key: key}); }
  readDecision(runId: string): Promise<Decision | null> { return this.request("GET", `${this.path(runId)}/decision`); }
  act(runId: string, action: Action): Promise<{status: string; [key: string]: unknown}> { return this.request("POST", `${this.path(runId)}/actions`, action); }
  finish(runId: string): Promise<Report> { return this.request("POST", `${this.path(runId)}/finish`); }

  private workflowPath(runId: string): string {
    if (!runId || runId.includes("/") || [".", ".."].includes(runId)) throw new Error("Invalid workflow identifier");
    return `/v1/workflows/${encodeURIComponent(runId)}`;
  }
  workflowCreate(spec: Record<string, unknown>): Promise<{run_id: string; agent_token: string; status: string}> {
    return this.request("POST", "/v1/workflows", {spec});
  }
  workflowList(): Promise<Record<string, unknown>[]> { return this.request("GET", "/v1/workflows"); }
  workflowGet(runId: string): Promise<Record<string, unknown>> { return this.request("GET", this.workflowPath(runId)); }
  workflowObserve(runId: string): Promise<Record<string, unknown>> { return this.request("POST", `${this.workflowPath(runId)}/observe`); }
  workflowInvoke(runId: string, operation: string, args: Record<string, unknown>, idempotencyKey: string, expectedDecisionId?: string): Promise<Record<string, unknown>> {
    return this.request("POST", `${this.workflowPath(runId)}/operations`, {
      operation, arguments: args, idempotency_key: idempotencyKey,
      ...(expectedDecisionId === undefined ? {} : {expected_decision_id: expectedDecisionId}),
    });
  }
  workflowAppeal(runId: string, idempotencyKey: string, expectedDecisionId: string): Promise<Record<string, unknown>> {
    return this.request("POST", `${this.workflowPath(runId)}/appeals`, {
      idempotency_key: idempotencyKey, expected_decision_id: expectedDecisionId,
    });
  }
  workflowFinish(runId: string): Promise<{run_id: string; status: string}> { return this.request("POST", `${this.workflowPath(runId)}/finish`); }
  workflowCancel(runId: string): Promise<Record<string, unknown>> { return this.request("POST", `${this.workflowPath(runId)}/cancel`); }
  workflowReport(runId: string): Promise<Record<string, unknown>> { return this.request("GET", `${this.workflowPath(runId)}/report`); }
}
