# IR 流程图与用户介入点（P0 实现版）

> 视角：**IR（Analysis Plan IR 及其 12 类对象）** 的产生、引用、版本化与消费——上一份文档（workflow-and-human-gates.md）讲"流水线怎么跑"，本文讲"数据对象怎么流转、人卡在哪条边上"。
> IR 铁律（proposal 7）：对象版本不可变（同版本同内容幂等、异内容拒绝）；引用精确版本、禁 `latest`；运行结果与检查记录**不写回**计划；文件路径只在存储层解析。

## 一、IR 对象流转图（谁产生 → 被谁引用 → 谁消费）

```mermaid
flowchart TB
    subgraph USERS ["👤 用户介入（橙）"]
        U1["①建约定：问题/两期/用途"]
        U2["②定口径+给数据：CSV/税/优惠/退款/覆盖/同店资格"]
        U3["③按确认：批准六类 digest"]
        U5["⑤审变更：旧值→新值→影响"]
        U7["⑦录因果候选 → to-verify"]
        U8["⑧导出勾选 / 模板命名"]
    end

    subgraph OBJECTS ["IR 对象（每对象: 稳定ID + 独立版本 + 内容摘要）"]
        AC["AnalysisContract<br/>业务约定/比较范围<br/>（引用精确版本）"]
        MC["MetricContract<br/>指标口径（unconfirmed 即拦确认）"]
        DSM["DataSnapshotManifest<br/>快照字节指纹/解析配置/覆盖"]
        BS["BindingSnapshot<br/>字段映射/维度/范围标签"]
        IR["AnalysisPlanIR ★<br/>六步方法/依赖/输出契约<br/>只引用上面四类@版本"]
        EM["ExecutionManifest<br/>方法版本/预算/允许路径<br/>由 IR 编译产生"]
        AP["Approval<br/>六类 digest 精确绑定<br/>含 user_action(cli/ui/test)"]
        RR["RunRecord<br/>运行轴：不写回 IR"]
        EB["EvidenceBundle<br/>产物逐文件摘要<br/>run_id+plan_digest 关联"]
        FD["Finding<br/>自动: 描述/数值拆解<br/>人工: to-verify(draft)"]
        CR["ChangeRequest<br/>提议/合入/撤回"]
        PT["PlanTemplate<br/>清污: 无旧批准/数据/结论"]
    end

    subgraph MACHINE ["机器门（无人工，不可越过）"]
        G2["G2: 能力/授权/指纹"]
        G3["G3: 对账/覆盖/证据复算"]
        IMM["不可变存储: 同版本异内容拒绝"]
    end

    U1 -->|create_draft| AC
    AC -.引用.-> IR
    U2 -->|bind| DSM & BS & MC
    DSM -.引用.-> IR
    BS -.引用.-> IR
    MC -.引用.-> IR
    IR -->|compile| EM
    EM -.digest.-> AP
    U3 ==>|approve·硬门| AP
    AP ==>|授权| G2
    G2 -->|放行| RR
    RR --> EB
    EB --> G3
    G3 -->|通过| FD
    U7 -.录入.-> FD
    FD -->|④人审: 接受/带限制/退回| FD
    U5 -->|审阅| CR
    CR -->|合入→新版本| AC & IR
    IR -->|旧版 SUPERSEDED| IR
    U8 -->|提取| PT
    PT -.实例化=新 plan_id@v1.-> IR
    IMM -.约束.- OBJECTS

    classDef user fill:#fff1e8,stroke:#e8643a,color:#bf4927,stroke-width:2px;
    classDef core fill:#e6f4f1,stroke:#328477,color:#123d36,stroke-width:2px;
    classDef obj fill:#f4f3ef,stroke:#9aa5b1,color:#34404e;
    classDef gate fill:#edf3f5,stroke:#263442,color:#263442,stroke-width:2px;
    class U1,U2,U3,U5,U7,U8 user;
    class IR core;
    class AC,MC,DSM,BS,EM,AP,RR,EB,FD,CR,PT obj;
    class G2,G3,IMM gate;
```

## 二、三条状态轴（分离保存，互不覆盖）

```mermaid
stateDiagram-v2
    state 计划轴 {
        [*] --> DRAFT: create_draft
        DRAFT --> NEEDS_INPUT: G0 有问题
        NEEDS_INPUT --> READY_FOR_REVIEW: G0 通过
        READY_FOR_REVIEW --> APPROVED: 👤③确认
        APPROVED --> SUPERSEDED: 新版本被确认
        note right of NEEDS_INPUT
            缺参数/缺引用/坏日期
            都停在这里，可存草稿
        end note
    }
    state 运行轴 {
        [*] --> QUEUED
        QUEUED --> PREFLIGHT: G2
        PREFLIGHT --> BLOCKED: 能力/授权/指纹不符
        PREFLIGHT --> RUNNING
        RUNNING --> COMPLETED: 约定计算完成
        RUNNING --> FAILED: 检查FAIL/步骤错误
        RUNNING --> TIMED_OUT: 预算
        note right of COMPLETED
            COMPLETED ≠ 验收通过
            （运行结果不写回计划）
        end note
    }
    state 验收轴 {
        [*] --> PENDING
        PENDING --> PASSED: G3 机器裁判
        PENDING --> FAILED: 必需检查FAIL/UNKNOWN
        PASSED --> ACCEPTED: 👤④
        PASSED --> ACCEPTED_WITH_LIMITATIONS: 👤④
        FAILED --> REJECTED: 👤④（唯一出路）
        note right of FAILED
            人的接受不能改写检查事实
        end note
    }
```

## 三、IR 迁移上的人工介入点

| IR 迁移 | 触发者 | 用户动作 | 不动作的后果 |
|---|---|---|---|
| 参数 → `AnalysisContract` | 用户① | 确认问题/两期/用途 | 契约缺口 → 计划停 `NEEDS_INPUT` |
| `MetricContract` 口径 | 用户② | 三口径选实值 | `unconfirmed` → G1 拒绝确认（T03） |
| → `BindingSnapshot`/`DataSnapshotManifest` | 用户② | 给数据+覆盖清单+可选同店资格；警告知悉 | 阻断项：绑定失败；警告项：确认时必须 ack |
| `AnalysisPlanIR: DRAFT→APPROVED` | **用户③（唯一路径）** | approve 六类 digest | 无授权 → 运行轴 `BLOCKED(not-authorized)` |
| 同 plan_id 实质变更 → 新版本 | 用户⑤ 审 CR | 看旧值/新值/影响 → 合入或撤回 | 新版本无确认（digest 复算自动失效旧授权） |
| 运行轴/验收轴 | 机器 | 不介入（预算/超时/门禁自动） | — |
| `Finding` 人审（ReviewRecord） | 用户④ | 接受/带限制/退回 | 验证 FAIL/UNKNOWN → 只能 REJECTED 或改计划 |
| → `to-verify` Finding | 用户⑦ | 录入因果候选 | 不录则不存在；自动发现永不产生因果类型 |
| → `PlanTemplate` | 用户⑧ | 命名提取（清污自动强制） | 模板只带方法/参数槽/适用条件 |
| 模板 → 新 `AnalysisPlanIR` | 用户⑥ | 下周期重新绑定 | 新 plan_id@v1，一切从 NEEDS_INPUT 重新走 |

## 四、与"模型工厂"视图的差异（一句话）

工厂视图回答"**流程怎么跑**"；IR 视图回答"**数据怎么变**"：每个对象只在明确的生产者手里诞生、只被精确版本引用、内容一变旧确认即失效——用户介入点从"流程节点"落到"**对象迁移的边**"上，其中只有 `AnalysisPlanIR → APPROVED` 一条边**必须**由人走出，其余介入点要么在对象诞生前（①②），要么在对象诞生后（④⑤⑥⑦⑧）。
