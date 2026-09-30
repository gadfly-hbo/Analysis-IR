import { StrictMode, useCallback, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { client } from "./api";

/* ---------- 共享任务上下文（当前计划跨页流转） ---------- */

interface TaskState {
  planId: string;
  planVersion: number;
  status: string;
  question: string;
  purpose: string;
  unresolved: string[];
}

const EMPTY_FORM = {
  question: "最近门店销售下降，分析原因",
  purpose: "品类策略评估（不自动决策）",
  baseStart: "2026-07-01", baseEnd: "2026-07-31",
  reportStart: "2026-08-01", reportEnd: "2026-08-31",
};

function App() {
  const [tab, setTab] = useState("projects");
  const [task, setTask] = useState<TaskState | null>(null);
  const [notice, setNotice] = useState("");
  const tabs = useMemo(
    () => [
      ["projects", "项目与模板"],
      ["plan", "分析计划"],
      ["data", "数据与口径"],
      ["confirm", "检查与确认"],
      ["run", "运行与验收"],
    ] as const,
    []
  );
  const go = useCallback((next: string, message = "") => {
    setTab(next);
    setNotice(message);
  }, []);

  return (
    <>
      <header className="topbar">
        <span className="brand">
          <span className="mark">◆</span> Analysis Plan Studio
        </span>
        <span className="note">先确认 AI 准备怎么分析，再检查它实际做了什么 · 本地运行</span>
      </header>
      <div className="layout">
        <nav className="sidebar">
          {tabs.map(([key, label]) => (
            <button
              key={key}
              className={tab === key ? "active" : ""}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </nav>
        <main className="main">
          {notice && <div className="card"><span className="note">{notice}</span></div>}
          {tab === "projects" && (
            <ProjectsPage task={task} onOpen={(t) => { setTask(t); go("plan", "已载入任务"); }} />
          )}
          {tab === "plan" && (
            <PlanPage task={task} onCreated={(t) => { setTask(t); go("data", "草稿已创建，下一步绑定数据与口径"); }} />
          )}
          {tab === "data" && task && (
            <DataPage task={task} onBound={() => go("confirm", "绑定完成，进入检查与确认")} />
          )}
          {tab === "confirm" && task && (
            <ConfirmPage task={task} onApproved={() => go("run", "确认记录已生成，可以启动运行")} />
          )}
          {tab === "run" && task && <RunPage task={task} />}
          {(tab === "data" || tab === "confirm" || tab === "run") && !task && (
            <p className="note">先在「项目与模板」或「分析计划」建立任务。</p>
          )}
        </main>
      </div>
      <footer className="statusbar">
        <span>状态：{task ? `${task.planId}@${task.planVersion} · ${task.status}` : "无任务"}</span>
        <span>数据不出本机 · 失败如实披露 · 人审不可绕过</span>
      </footer>
    </>
  );
}

/* ---------- 页面 1：项目与模板 ---------- */

function ProjectsPage(props: { task: TaskState | null; onOpen: (t: TaskState) => void }) {
  const [plans, setPlans] = useState<{ plan_id: string; plan_version: number; status: string }[]>([]);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    try {
      setPlans((await client.listPlans()).plans);
    } catch (e) {
      setError(String(e));
    }
  }, []);
  useMemo(() => void refresh(), [refresh]);
  return (
    <>
      <h2 className="view-title">项目与模板</h2>
      <div className="card">
        <h3>内置模板：销售差异与多维贡献拆解</h3>
        <p className="note">
          适用边界：单一事实表（行级销售记录）、单一币种、同口径两期比较。
          结论类型限于描述与数值拆解；因果解释进入待验证区，不自动输出经营动作。
        </p>
      </div>
      <div className="card">
        <h3>计划列表</h3>
        {error && <p className="issue issue-warn">{error}</p>}
        <table>
          <thead>
            <tr><th>计划</th><th>状态</th><th /></tr>
          </thead>
          <tbody>
            {plans.map((p) => (
              <tr key={`${p.plan_id}@${p.plan_version}`}>
                <td className="mono">{p.plan_id}@{p.plan_version}</td>
                <td><StatusChip status={p.status} /></td>
                <td>
                  <button
                    className="secondary"
                    onClick={() =>
                      props.onOpen({
                        planId: p.plan_id, planVersion: p.plan_version,
                        status: p.status, question: "", purpose: "", unresolved: [],
                      })
                    }
                  >打开</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function StatusChip({ status }: { status: string }) {
  const cls =
    status === "APPROVED" ? "chip-ok"
    : status === "NEEDS_INPUT" || status === "DRAFT" ? "chip-warn"
    : status === "READY_FOR_REVIEW" ? "chip-accent"
    : status === "SUPERSEDED" ? "chip-gray"
    : "chip-run";
  return <span className={`chip ${cls}`}>{status}</span>;
}

/* ---------- 页面 2：分析计划 ---------- */

function PlanPage(props: { task: TaskState | null; onCreated: (t: TaskState) => void }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [result, setResult] = useState<{ unresolved: string[]; plan: Record<string, unknown> } | null>(null);
  const [showJson, setShowJson] = useState(false);
  const [error, setError] = useState("");
  const update = (key: keyof typeof EMPTY_FORM) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: e.target.value });

  const create = async () => {
    setError("");
    try {
      const res = await client.createPlan(
        {
          question: form.question,
          decision_purpose: form.purpose,
          base_period: { start: form.baseStart, end: form.baseEnd },
          report_period: { start: form.reportStart, end: form.reportEnd },
        },
        "analyst-a"
      );
      setResult({ unresolved: res.unresolved, plan: res.plan });
      props.onCreated({
        planId: res.plan.plan_id, planVersion: res.plan.plan_version,
        status: res.status, question: form.question, purpose: form.purpose,
        unresolved: res.unresolved,
      });
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <>
      <h2 className="view-title">分析计划</h2>
      <div className="card">
        <h3>业务约定（草稿不等于承诺）</h3>
        <div className="field">
          <label>业务问题</label>
          <input value={form.question} onChange={update("question")} />
        </div>
        <div className="field">
          <label>决策用途（工具不代替经营决定）</label>
          <input value={form.purpose} onChange={update("purpose")} />
        </div>
        <div className="row">
          <div className="field">
            <label>基期开始 / 结束</label>
            <input value={form.baseStart} onChange={update("baseStart")} />
            <input value={form.baseEnd} onChange={update("baseEnd")} />
          </div>
          <div className="field">
            <label>报告期开始 / 结束</label>
            <input value={form.reportStart} onChange={update("reportStart")} />
            <input value={form.reportEnd} onChange={update("reportEnd")} />
          </div>
        </div>
        <button className="primary" onClick={create}>从模板创建草稿</button>
        {error && <p className="issue issue-block">{error}</p>}
      </div>
      {result && (
        <div className="card">
          <h3>可读计划卡片（与机器执行对象同源）</h3>
          <p>
            在确认的范围与口径下，计算整体变化，识别不同门店、品类对应的数值贡献；
            列出尚不能验证的解释。
          </p>
          <p className="note">
            基期 {form.baseStart} ~ {form.baseEnd}；报告期 {form.reportStart} ~ {form.reportEnd}。
            六步方法：质量检查 → 比较范围 → 整体差异 → 门店拆解 → 品类拆解 → 对账交付。
          </p>
          {result.unresolved.length > 0 && (
            <div className="issue issue-warn">
              待确认：{result.unresolved.join("；")}（未确认内容不会静默成为正式事实）
            </div>
          )}
          <button className="secondary" onClick={() => setShowJson(!showJson)}>
            {showJson ? "隐藏" : "查看"} IR（高级入口）
          </button>
          {showJson && <pre className="json">{JSON.stringify(result.plan, null, 2)}</pre>}
        </div>
      )}
    </>
  );
}

/* ---------- 页面 3：数据与口径 ---------- */

const TREATMENT_OPTIONS = ["unconfirmed", "net-of-tax", "gross"] as const;

function DataPage(props: { task: TaskState; onBound: () => void }) {
  const [sourceFile, setSourceFile] = useState("");
  const [dates, setDates] = useState("");
  const [treatments, setTreatments] = useState({ tax: "net-of-tax", discounts: "after-discount", returns_attribution: "transaction-period" });
  const [warnings, setWarnings] = useState<string[] | null>(null);
  const [error, setError] = useState("");

  const bind = async () => {
    setError("");
    try {
      const res = await client.bind(
        props.task.planId, props.task.planVersion, sourceFile, treatments,
        dates.split(",").map((d) => d.trim()).filter(Boolean)
      );
      setWarnings(res.warnings);
      if (res.warnings.length === 0) props.onBound();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <>
      <h2 className="view-title">数据与口径</h2>
      <div className="card">
        <h3>导入本地事实表（复制为项目快照并计算指纹）</h3>
        <div className="field">
          <label>CSV 文件路径（本机）</label>
          <input placeholder="/path/to/sales.csv" value={sourceFile}
                 onChange={(e) => setSourceFile(e.target.value)} />
        </div>
        <div className="field">
          <label>覆盖清单日期（逗号分隔，如 2026-07-05,2026-07-15…；留空表示无覆盖证明）</label>
          <input value={dates} onChange={(e) => setDates(e.target.value)} />
        </div>
        {(["tax", "discounts", "returns_attribution"] as const).map((key) => (
          <div className="field" key={key}>
            <label>{key === "tax" ? "税口径" : key === "discounts" ? "优惠口径" : "退款归属"}</label>
            <select
              value={treatments[key]}
              onChange={(e) => setTreatments({ ...treatments, [key]: e.target.value })}
            >
              {key === "tax" &&
                TREATMENT_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
              {key === "discounts" &&
                ["unconfirmed", "after-discount", "before-discount"].map((o) =>
                  <option key={o} value={o}>{o}</option>)}
              {key === "returns_attribution" &&
                ["unconfirmed", "transaction-period", "original-period"].map((o) =>
                  <option key={o} value={o}>{o}</option>)}
            </select>
          </div>
        ))}
        <button className="primary" disabled={!sourceFile} onClick={bind}>
          校验并绑定（缺失/混合币种/日期错误将被阻断）
        </button>
        {error && <p className="issue issue-block">{error}</p>}
        {warnings && warnings.length > 0 && (
          <div className="issue issue-warn">
            {warnings.map((w) => <div key={w}>{w}</div>)}
            <button className="secondary" onClick={props.onBound}>已知悉，继续（警告保留在确认记录）</button>
          </div>
        )}
        <p className="note">
          比较范围默认「全量观察范围」；提供门店资格清单才构成同店口径（不自动把门店交集当同店）。
        </p>
      </div>
    </>
  );
}

/* ---------- 页面 4：检查与确认 ---------- */

function ConfirmPage(props: { task: TaskState; onApproved: () => void }) {
  const [report, setReport] = useState<{ g0_passed: boolean; issues: { code: string; location: string; message: string }[] } | null>(null);
  const [approval, setApproval] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");

  const validate = async () => {
    setError("");
    try { setReport(await client.validate(props.task.planId, props.task.planVersion)); }
    catch (e) { setError(String(e)); }
  };
  const approve = async () => {
    setError("");
    try {
      await client.compile(props.task.planId, props.task.planVersion);
      const res = await client.approve(props.task.planId, props.task.planVersion);
      setApproval(res.approval);
      props.onApproved();
    } catch (e) { setError(String(e)); }
  };

  return (
    <>
      <h2 className="view-title">检查与确认</h2>
      <div className="card">
        <h3>G0 计划完整性</h3>
        <button className="secondary" onClick={validate}>执行检查</button>
        {report && (
          report.g0_passed
            ? <span className="chip chip-ok" style={{ marginLeft: 10 }}>通过</span>
            : report.issues.map((i) => (
                <div key={`${i.code}-${i.location}`} className="issue issue-block">
                  {i.code} · {i.location}：{i.message}
                </div>
              ))
        )}
      </div>
      <div className="card">
        <h3>确认（批准的是一组精确绑定，不是一句“同意”）</h3>
        <p className="note">
          确认对象包括：业务约定、计划、指标与绑定、数据快照、方法与检查注册表、执行清单——
          各自的内容摘要（digest）。任何实质变更都会使旧确认失效并要求重新确认。
        </p>
        <button className="primary" onClick={approve} disabled={report ? !report.g0_passed : false}>
          编译执行清单并确认计划
        </button>
        {error && <p className="issue issue-block">{error}</p>}
        {approval && (
          <pre className="json">{JSON.stringify(approval, null, 2)}</pre>
        )}
      </div>
    </>
  );
}

/* ---------- 页面 5：运行与验收 ---------- */

function RunPage(props: { task: TaskState }) {
  const [run, setRun] = useState<{ run_id: string; status: string; steps: { step_id: string; status: string }[] } | null>(null);
  const [verification, setVerification] = useState<{ status: string; checks: { name: string; status: string; detail: string }[]; limitations: string[] } | null>(null);
  const [findings, setFindings] = useState<{ finding_id: string; type: string; statement: string; evidence_item_ids: string[] }[]>([]);
  const [acceptance, setAcceptance] = useState("");
  const [error, setError] = useState("");
  const call = (fn: () => Promise<void>) => async () => {
    setError("");
    try { await fn(); } catch (e) { setError(String(e)); }
  };

  return (
    <>
      <h2 className="view-title">运行与验收</h2>
      <div className="card">
        <h3>受限执行（固定方法 · 预算强制 · 无网络）</h3>
        <button
          className="primary"
          onClick={call(async () => {
            const res = await client.startRun(props.task.planId, props.task.planVersion);
            setRun(res.run);
          })}
        >启动运行</button>
        {run && (
          <table style={{ marginTop: 10 }}>
            <thead><tr><th>步骤</th><th>状态</th></tr></thead>
            <tbody>
              {run.steps.map((s) => (
                <tr key={s.step_id}>
                  <td>{s.step_id}</td>
                  <td><span className={`chip ${s.status === "DONE" ? "chip-ok" : s.status === "SKIPPED" || s.status === "FAILED" ? "chip-fail" : "chip-run"}`}>{s.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {run && run.status === "COMPLETED" && (
        <>
          <div className="card">
            <h3>证据与验证（运行完成 ≠ 验证通过 ≠ 用户接受）</h3>
            <button
              className="secondary"
              onClick={call(async () => {
                await client.collect(run.run_id);
                setVerification((await client.verify(run.run_id)).verification);
                setFindings((await client.findings(run.run_id)).findings);
              })}
            >回收证据并验证</button>
            {verification && (
              <>
                <p>
                  验证状态：
                  <span className={`chip ${verification.status === "PASSED" ? "chip-ok" : "chip-fail"}`}>
                    {verification.status}
                  </span>
                </p>
                {verification.checks.map((c) => (
                  <div key={c.name} className="note">
                    {c.name}：{c.status} —— {c.detail}
                  </div>
                ))}
                {verification.limitations.map((l) => (
                  <div key={l} className="issue issue-warn">限制：{l}</div>
                ))}
              </>
            )}
          </div>
          {verification && verification.status === "PASSED" && (
            <div className="card">
              <h3>人工验收</h3>
              <button
                className="primary"
                onClick={call(async () => {
                  await client.accept(run.run_id, "ACCEPTED");
                  setAcceptance("已接受");
                })}
              >接受</button>{" "}
              <button
                className="secondary"
                onClick={call(async () => {
                  await client.accept(run.run_id, "ACCEPTED_WITH_LIMITATIONS");
                  setAcceptance("带限制接受");
                })}
              >带限制接受</button>{" "}
              <button
                className="secondary"
                onClick={call(async () => {
                  await client.accept(run.run_id, "REJECTED");
                  setAcceptance("已退回");
                })}
              >退回</button>
              {acceptance && <span className="chip chip-accent" style={{ marginLeft: 10 }}>{acceptance}</span>}
            </div>
          )}
          {findings.length > 0 && (
            <div className="card">
              <h3>发现（数值均绑定证据；因果解释进入待验证区）</h3>
              {findings.map((f) => (
                <div key={f.finding_id} style={{ marginBottom: 8 }}>
                  <span className="chip chip-gray">{f.type}</span> {f.statement}{" "}
                  {f.evidence_item_ids.map((id) => (
                    <span key={id} className="evidence-chip" title={`证据 ${id}`}>{id}</span>
                  ))}
                </div>
              ))}
              <button
                className="secondary"
                onClick={call(async () => {
                  const res = await client.exportPackage(props.task.planId, props.task.planVersion, "handover");
                  setAcceptance(`交接包已导出：${res.package}`);
                })}
              >导出计划交接包（默认不含原始数据）</button>
            </div>
          )}
        </>
      )}
      {error && <p className="issue issue-block">{error}</p>}
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
