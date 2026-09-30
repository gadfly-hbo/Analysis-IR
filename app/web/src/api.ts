/** 本地 API 客户端：回环服务 + 会话令牌（proposal 10.5）。 */

export interface PlanSummary {
  plan_id: string;
  plan_version: number;
  status: string;
}

export interface ValidationIssue {
  code: string;
  location: string;
  message: string;
}

export interface Plan {
  plan_id: string;
  plan_version: number;
  [key: string]: unknown;
}

let cachedToken: string | null = null;

async function token(): Promise<string> {
  if (cachedToken) return cachedToken;
  const res = await fetch("/api/session-token");
  const fetched: string = (await res.json()).token;
  cachedToken = fetched;
  return fetched;
}

export async function api<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("X-APS-Token", await token());
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail.slice(0, 300)}`);
  }
  return res.json() as Promise<T>;
}

export const apiGet = <T>(path: string) => api<T>(path);

export const apiPost = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });

export const client = {
  listPlans: () => apiGet<{ plans: PlanSummary[] }>("/api/plans"),
  createPlan: (params: Record<string, unknown>, operator: string) =>
    apiPost<{ plan: Plan; contract: Record<string, unknown>; unresolved: string[]; status: string }>(
      "/api/plans", { template: "sales-delta", params, operator }
    ),
  getPlan: (id: string, version: number) =>
    apiGet<{ plan: Plan }>(`/api/plans/${id}/${version}`),
  validate: (id: string, version: number) =>
    apiPost<{ g0_passed: boolean; status: string; issues: ValidationIssue[] }>(
      `/api/plans/${id}/${version}/validate`, {}
    ),
  bind: (id: string, version: number, sourceFile: string, treatments: Record<string, string>, dates: string[], eligibility?: { file: string; ruleNote: string }) =>
    apiPost<{ warnings: string[]; binding: { scope: { mode: string; label: string; same_store: boolean } } }>(`/api/bindings`, {
      plan_id: id, plan_version: version, source_file: sourceFile,
      metric_treatments: treatments,
      coverage: { kind: dates.length ? "manifest-ref" : "unverified", dates },
      store_eligibility: eligibility?.file
        ? { list_file: eligibility.file, rule_note: eligibility.ruleNote || "用户提供的门店资格清单" }
        : undefined,
      operator: "analyst-a",
    }),
  compile: (id: string, version: number) =>
    apiPost(`/api/plans/${id}/${version}/compile`, {}),
  approve: (id: string, version: number, acks: { code: string; note?: string }[] = []) =>
    apiPost<{ approval: Record<string, unknown> }>(`/api/plans/${id}/${version}/approve`, {
      operator: "analyst-a", action: "approve", origin: "ui", warnings_acknowledged: acks,
    }),
  getContract: (id: string, version: number) =>
    apiGet<{ contract: { id: string; comparison_scope?: Record<string, unknown> } }>(
      `/api/contracts/${id}/${version}`
    ),
  withdrawChange: (crId: string) =>
    apiPost(`/api/changes/${crId}/withdraw`, { operator: "analyst-a" }),
  diff: (id: string, va: number, vb: number) =>
    apiGet<{ entries: { path: string; old: unknown; new: unknown; impact: string }[] }>(
      `/api/plans/${id}/diff/${va}/${vb}`
    ),
  startRun: (id: string, version: number) =>
    apiPost<{ run: { run_id: string; status: string; steps: { step_id: string; status: string }[] } }>(
      "/api/runs", { plan_id: id, plan_version: version, operator: "analyst-a" }
    ),
  collect: (runId: string) =>
    apiPost(`/api/runs/${runId}/evidence/collect`, {}),
  verify: (runId: string) =>
    apiPost<{ verification: { status: string; checks: { name: string; status: string; detail: string }[]; limitations: string[] } }>(
      `/api/runs/${runId}/evidence/verify`, {}
    ),
  accept: (runId: string, decision: string) =>
    apiPost(`/api/runs/${runId}/accept`, { decision, operator: "analyst-a" }),
  findings: (runId: string) =>
    apiGet<{ findings: { finding_id: string; type: string; statement: string; evidence_item_ids: string[] }[] }>(
      `/api/runs/${runId}/findings`
    ),
  generateFindings: (runId: string) =>
    apiPost<{ findings: { finding_id: string; type: string; statement: string; evidence_item_ids: string[] }[] }>(
      `/api/runs/${runId}/findings/generate`, {}
    ),
  addToVerify: (runId: string, text: string) =>
    apiPost<{ finding: { finding_id: string } }>(`/api/runs/${runId}/to-verify`, { text }),
  proposeChange: (planId: string, planVersion: number, reason: string, changes: unknown[]) =>
    apiPost<{ change_request: { cr_id: string } }>("/api/changes", {
      plan_id: planId, plan_version: planVersion, reason, proposed_by: "user", changes,
    }),
  mergeChange: (crId: string) =>
    apiPost<{ merged: { merged_plan_version?: number } }>(`/api/changes/${crId}/merge`, {
      operator: "analyst-a",
    }),

  exportPackage: (id: string, version: number, mode: string) =>
    apiPost<{ package: string }>("/api/exports", {
      plan_id: id, plan_version: version, mode, operator: "analyst-a",
    }),
};
