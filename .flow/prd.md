# PRD：Analysis Plan Studio P0（独立工具 MVP）

> 事实源链：`.flow/proposal.md`（产品方案 v1.0，最高权威）> 本 PRD（细化与落地决策）。
> 本 PRD 覆盖 proposal 第 13.1 节定义的 **P0 阶段**（M0—M3 工程顺序）；P1/P2 见 Out of Scope。
> 发布方式：项目未配置 issue tracker（目录为空、非 git 仓库），按 dev-flow 降级路径写入 `.flow/prd.md`。

## Problem Statement

分析人员把一句需求直接交给 coding agent 或自己写 SQL/Python，口径分歧（退款归属、比较范围、同店资格）没有在执行前暴露，Agent 静默改样本、换指标后产出一份看起来完整的报告；不会审代码的业务负责人无法判断约定事项是否真的被执行；下一个周期复用的是旧提示词和旧报告，连同旧口径旧结论一起复制。用户需要的是：**分析前有一份可读、可确认的约定；分析中变更留痕；分析后能按约定核对证据；下一次能复用方法而不是复用结论。**

## Solution

一个 macOS 本机运行的独立小工具（Analysis Plan Studio）：用户从内置模板（销售差异与多维贡献拆解）创建结构化分析计划，导入本地事实表并完成字段映射与指标口径确认，通过五道门禁检查后显式确认计划；受限本地 Runner 用版本锁定的固定方法执行六步分析（质量检查→比较范围→整体差异→门店拆解→品类拆解→对账交付），回收带指纹的证据并按原约定验收；结果可导出、方法可存为参数化模板供下一周期复用。全程离线可用，不依赖 JuanerAI、Xanthil 或任何 LLM 服务。机器执行对象与用户阅读对象同源（同一份 AnalysisPlanIR），确认的是一组精确绑定（digest 摘要）而非一句"同意"。

## User Stories

**计划编制（F01）**
1. As an 分析人员, I want 从"销售差异与多维贡献拆解"模板创建分析计划草稿, so that 我不必从零设计步骤和方法。
2. As an 分析人员, I want 在结构化表单中填写分析目的、决策用途、比较范围与输出要求, so that 计划承载业务约定而不是一句模糊需求。
3. As an 分析人员, I want 保存未完成的草稿并随时回来继续, so that 缺少信息时不会被强制一次性填完。
4. As an 分析人员, I want 对"最近""同店"等模糊表达只收到候选解释而不是工具擅自定值, so that 没有确认来源的内容不会变成正式事实。
5. As an 分析人员, I want 在没有绑定数据时也能编写计划但界面显示"未绑定/不可执行", so that 计划编写与数据准备可以并行且状态不误导。
6. As an 分析人员, I want 一个业务版可读计划卡片（而非 JSON）, so that 我和业务负责人都能审阅真实口径。

**数据与口径（F02、F03）**
7. As an 分析人员, I want 导入本地 CSV/Parquet 事实表并看到类型、行数、关键字段检查结果, so that 数据问题在绑定前暴露。
8. As an 分析人员, I want 把业务字段（row_id/business_date/store_id/category_id/net_sales_amount/currency）映射到文件列, so that 工具知道每个约定字段从哪来。
9. As an 分析人员, I want 显式确认指标口径（税、优惠、退款归属）与比较范围（基期/报告期、门店资格）, so that 这些口径成为版本化契约而不是隐含假设。
10. As an 分析人员, I want 导入的数据被复制为项目快照并生成内容指纹, so that 执行不受外部文件改动影响且事后可核验。
11. As an 分析人员, I want 提供（或确认缺失的）数据覆盖清单, so that "没有记录"不会被当成"零销售"。
12. As an 分析人员, I want 使用同店口径时必须提供门店资格清单及规则, so that 结果不会被包装成名不副实的"同店分析"。

**检查与确认（F04、F05、F06）**
13. As an 分析人员, I want 草稿在确认前经过结构、引用、依赖、绑定、方法能力检查并得到可定位到字段/步骤的问题清单, so that 我知道必须修什么才能确认。
14. As an 分析人员, I want 确认动作生成一条独立确认记录（谁、何时、批准了哪些版本的哪些 digest）, so that 确认不靠对话文字推断。
15. As an 分析人员, I want 确认前看到执行权限摘要（会用哪个 Runner、哪些方法版本、哪些数据快照）, so that 我知道自己在授权什么。
16. As an 分析人员, I want 任何实质变更（期间、过滤、指标、方法、验证、权限）生成新版本并要求重新确认, so that 旧确认永远不会授权新内容。
17. As an 分析人员, I want 语义级版本差异视图（旧值 vs 新值 + 影响范围）, so that 重新确认时不必逐字段对比 JSON。
18. As an 分析人员, I want 仅改标题/备注时只写注释记录而不产生新版本, so that 无实质影响的编辑不制造确认负担。

**执行（F07、F08）**
19. As an 分析人员, I want 一键启动已确认计划并由受限本地 Runner 按固定方法完成六步分析, so that 计算过程受控、可回执。
20. As an 分析人员, I want 超时、取消、失败时后续依赖步骤停止且已有部分证据保留, so that 中断不是黑箱也不是全损。
21. As an 分析人员, I want 复跑创建新 run_id 而不覆盖旧运行记录, so that 历史可追溯。
22. As an 分析人员, I want 执行前核验快照指纹与方法/规则版本匹配, so that 数据或实现被调包时运行被阻断。

**验收（F09）**
23. As an 分析人员, I want 运行完成后看到分项验证状态（对账、覆盖、证据完整性各是 PASS/WARN/FAIL/UNKNOWN）, so that "跑完了"不等于"通过了"。
24. As an 业务负责人, I want 每条数值发现都能跳转到对应证据产物, so that 我不用审代码也能核对关键数字。
25. As an 业务负责人, I want 结果页分开显示"运行完成/验证通过/用户接受"三个状态, so that 我不会被一个绿色成功标记误导。
26. As an 分析人员, I want 把结果标记为接受/带限制接受/退回, so that 人工判断被记录且不覆盖机器检查事实。
27. As an 业务负责人, I want 无证据的因果式结论（"断货导致下降"）进入待验证问题区而不是结论区, so that 报告不会夹带未证实的解释。

**输出与复用（F10、F11）**
28. As an 分析人员, I want 导出计划交接包（默认不含原始数据）与分析交付包, so that 审阅者离线可读且敏感数据不外泄。
29. As an 分析人员, I want 保存参数化模板（方法、参数槽位、验证规则）并自动清除旧批准、旧数据、旧结论, so that 复用方法不污染新任务。
30. As an 分析人员, I want 用下一周期数据实例化模板并重新绑定确认, so that 每周期分析从数小时缩短到分钟级。
31. As an 分析人员, I want 导入外部计划交接包时版本、引用、摘要被核验且越界路径被拒绝, so that 外部包按不可信输入处理。

**草稿导入（F12）**
32. As an 分析人员, I want 导入 Agent 生成的标准 JSON 草稿但其中夹带的自由 SQL/代码/远程路径被拒绝并定位违规内容, so that Agent 加速起草但不扩大执行面。

**运行环境**
33. As an 分析人员, I want 全流程在断网、无 LLM 账户的机器上完成, so that 工具可用性不依赖外部服务。

## Implementation Decisions

以下决策中标注〔proposal 已定〕的直接来自方案；标注〔PRD 新增〕为本 PRD 补充的落地细节，已在 dev-flow PRD diff gate 单独列出待确认。

1. **交付范围 = proposal 13.1 的 P0**〔proposal 已定〕：本地界面、基础契约、固定场景（销售差异与多维贡献拆解）、受限 Runner、版本/确认、证据与模板。M0（契约与金标准样例）→ M1（机器闭环，测试入口）→ M2（五个页面）→ M3（失败路径验证）按依赖序推进。
2. **仓库布局**〔proposal 已定，10.2 建议目录采纳为实际结构〕：`contracts/`（Schema 与测试向量）、`toolkit/`（计划/差异/验证/模板/批准摘要）、`adapters/local_context|local_runner|local_evidence|local_store`、`methods/`（版本锁定方法与检查实现）、`app/`（界面与本地服务）、`tests/golden|failures|conformance`。
3. **后端语言与运行时**〔PRD 新增〕：Python 3.12+，uv 管理依赖，FastAPI 提供仅绑定回环地址的本地服务（会话令牌 + 来源检查）。契约/检查/方法逻辑全部位于 `toolkit/` 与 `methods/`，`app/` 只做薄壳调用，不复制规则。
4. **契约权威与校验**〔PRD 新增，落地 proposal"前后端从同一规范生成类型或校验入口"〕：schema-first——`contracts/` 下的 JSON Schema Draft 2020-12 文件是唯一契约权威；Python 侧用 `jsonschema` 库在对象边界校验，不再用第二套模型（如 pydantic 模型）重新定义字段权威。format 断言按 proposal 7.2 要求以错误日期、未知枚举、额外字段、错误引用做真实测试。
5. **摘要与指纹**〔PRD 新增，落地 proposal 7.3"固定、版本化的规范化规则"〕：对象摘要采用 RFC 8785 JSON Canonicalization Scheme（JCS）规范化后取 SHA-256；数据快照对原始字节取 SHA-256。规范化规则版本号（如 `canonicalization@1`）写入确认记录，`tests/conformance/` 提供测试向量。
6. **受限 Worker 形态**〔PRD 新增，落地 proposal 10.1"独立受限 Worker"〕：Runner 为独立 Python 子进程，经 stdin/stdout JSON-lines 协议通信；父进程（Run Controller）持有路径允许清单（仅项目内快照与运行输出目录）、时间与内存预算，超时/取消用进程终止实现；DuckDB 仅在 Worker 内以受控导入（先校验后注册的固定读入路径）方式使用，SQL 由方法模板生成、参数走类型化操作符绑定，不存在用户可控的 SQL 字符串拼接。
7. **项目状态存储**〔PRD 新增，落地 proposal 10.1 SQLite 参考存储〕：SQLite（Python 标准库 `sqlite3`，WAL 模式）保存对象索引、状态轴（计划/运行/验收）与运行历史；大对象（快照、结果表、证据文件）落项目目录文件系统，库中只存引用与指纹。输出先写暂存目录、校验完整后发布清单，防索引指向缺失产物。
8. **前端**〔PRD 新增，落地 proposal"前端复用成熟界面技术"〕：TypeScript + React + Vite 单页应用，由本地服务静态托管，五个页面按 proposal 5.1；JSON/IR 视图作为高级入口。所有按钮调用与 M1 测试入口完全相同的共享服务边界（proposal 9.2 的九个接口），UI 不自建规则、不伪造通过状态。视觉基线遵循用户全局设计规范 `~/.zcode/design/DESIGN.md`（Xanthil 桌面工作台设计语言）。
9. **核心对象与门禁**〔proposal 已定〕：12 类对象（AnalysisContract/MetricContract/BindingSnapshot/DataSnapshotManifest/AnalysisPlanIR/ExecutionManifest/Approval/RunRecord/EvidenceBundle/Finding·ReviewRecord/PlanTemplate/ChangeRequest）、五道门禁（G0—G4）、三条状态轴、检查状态五值（PASS/FAIL/WARN/UNKNOWN/NOT_APPLICABLE）按 proposal 第 7、8 章执行，字段命名与最小集合以 proposal 附录 B 为起点。
10. **方法与检查注册表**〔proposal 已定〕：`methods/` 内版本锁定的固定方法集（quality/scope/period-delta/group-delta/reconcile）与检查集（quality-and-coverage/group-reconciliation/evidence-coverage），语义按 proposal 第 4 章：基期≤0 不输出普通增长率、总变化近零不用 Δg/Δ 排名、切片贡献不相加、对账按定点金额精确比较。
11. **发现类型**〔proposal 已定〕：首期自动发现仅允许 `descriptive` 与 `arithmetic-decomposition`；因果含义文本进入待验证区，不做关键词过滤式"已解决"声明。
12. **导出与导入**〔proposal 已定，10.3〕：三种包（计划交接/分析交付/本地复现），导入按不可信输入处理（版本、大小、引用、摘要核验，拒绝路径穿越与携带代码）。
13. **无 LLM 路径**〔proposal 已定〕：默认关闭联网与模型调用；F12 草稿导入是标准 JSON 文件解析，不做模型集成。

## Testing Decisions

- **测试哲学**：只测外部行为——通过 proposal 9.2 的服务接口（PlanService/ContextPort/ApprovalService/RunnerPort/EvidencePort/TemplateService/ExportService）驱动，不测内部函数；金标准数值必须可人工手算核对（如 proposal 4.5 的 500k/300k/200k 合成数据）。
- **测试接缝（seam）**：主接缝为 toolkit 服务层（进程内直接调用，M1 起即存在）；次接缝为一条贯穿闭环的端到端测试（创建→绑定→校验→确认→执行→证据回收→验收→模板复用→二次运行），走与 UI 相同的调用路径。HTTP 层只做薄壳冒烟（回环、令牌、来源检查），不在 HTTP 层重复业务断言。
- **目录与内容**：`tests/golden/` 正常样例（输入—预期结果—预期检查清单三元组）；`tests/failures/` 失败样例，直接映射 proposal 14.1 的 T01—T24 场景（越权草稿、重复 row_id、伪回执、快照调包、覆盖缺口等）；`tests/conformance/` 摘要规范化测试向量（JCS + SHA-256）与 Schema 校验负例（错误日期 format、未知枚举、额外字段、坏引用）。
- **无先例可循**（新仓库）：以上结构即首套测试约定，后续以此为先例。
- **验收基线**：proposal 14.1 T01—T24 是 P0 验收矩阵的最终裁判，14.2 三条硬门槛（完成一次正常分析、阻断关键失败路径、复用一次下一周期分析）必须全过。

## Out of Scope

- **P1**：LLM 自然语言起草/解释（F13）、外部 Runner 回执接入与一致性测试（F14）。
- **P2**：JuanerAI / Xanthil / Semantic Context Runtime / Harness 集成（F15）。
- 企业能力：多租户、部门权限、双人审批、企业本体/语义服务、任意数据库连接、分布式执行、Docker 强制依赖。
- 任意生成代码执行、拖拽式流程画布、精美 PPT/DOCX 编排、自动因果推断、自动经营决策执行。
- 桌面应用封装（.app 打包、自动更新）——首期为本地 Web 界面，封装按使用反馈后置（proposal 10.1）。
- 性能基线承诺：10 万/100 万行测试点按 proposal 14.4 执行并如实记录，但不作为本 flow 的退出条件。

## Further Notes

- 红队报告（`.flow/red-team.md`）结论 go；KA1（用户前置确认意愿）建议以纸质确认卡试验验证，可与 M0 并行，不阻塞工程。
- proposal 14.3 的产品价值试用（10 次真实任务）属于发布后活动，不在本 flow 交付定义内。
- 本 PRD 的〔PRD 新增〕决策点已在 dev-flow PRD diff gate 呈现给用户确认；确认记录见 `.flow/state.json` history。

## GRILL 决议（开放问题自答记录，2026-09-30）

以下为 proposal 未定、实现必须回答的开放问题。按 dev-flow GRILL 规则自答并记录；全部为 additive，无一项与 proposal 冲突，故无需升级。

| # | 开放问题 | 决议（含理由） |
|---|---|---|
| G1 | M1"测试入口"形态 | toolkit 服务为纯进程内 Python 模块（零 HTTP 依赖），配一个人类可用的 CLI 脚本；FastAPI 层 M2 才引入。理由：proposal M1"先用测试入口完成机器闭环"，进程内边界即 M2 后 UI 复用的同一服务边界。 |
| G2 | 无 UI 阶段的"真实用户动作"来源 | `ApprovalService.approve` 必须接收显式 user_action 上下文（操作者标识+动作类型+来源标记 cli/ui/test）；服务自身永不生成确认。操作者标识 = 建项目时录入的本地用户名（项目设置项，非身份系统）。理由：proposal 7.1"模型不能自行创建有效确认"，来源标记让 M1 测试与 M2 UI 共用同一接口且记录可区分。 |
| G3 | 版本粒度 | 同 `plan_id` 实质变更 → `plan_version` +1 并失效旧确认；仅标题/备注 → 独立注释记录；模板实例化 → 新 `plan_id` 从 v1 起。理由：proposal 5.3/5.5 的直接落实。 |
| G4 | 覆盖清单缺失时的行为 | G1 门禁 WARN（用户须显式确认"无覆盖证明"）；运行可执行，但 coverage 检查结果为 UNKNOWN，必需检查 UNKNOWN → 阻断一切接受（含带限制接受）。要放行只能以新计划版本降级该检查并披露。理由：proposal 8.2"UNKNOWN 不能等同通过"+T07"不把缺记录补成零后宣称完整通过"；带限制接受仅适用于 WARN。 |
| G5 | 同店资格落地 | 可选门店资格清单文件（store_id 列表+规则说明文本）纳入 BindingSnapshot；无清单 → 比较范围明确标注"全量观察范围"，界面与结果页均不得出现"同店"表述。理由：proposal 4.2/T08。 |
| G6 | 计算引擎统一性 | Worker 内统一 DuckDB（导入、质量检查、聚合全走 DuckDB），不引入 pandas 聚合路径。理由：减少双引擎数值差异风险，对账可精确复现。 |
| G7 | 资源预算默认值 | run 级超时 600s、step 级 300s、Worker RLIMIT_AS 2 GiB、临时空间 1 GiB；均可被 ExecutionManifest 覆盖；默认值只写入运行记录不进契约。理由：proposal 8.1 G2/10.5 要求预算强制但未给数；给保守可运行初值。 |
| G8 | 项目级口径配置 | 单币种/项目（建项目时显式选择，缺省不猜）；时区默认 Asia/Shanghai 但首次须显式确认；金额定点 2 位小数（分），同币种对账精确比较不容差。理由：proposal 4.2/4.4；混合币种无换算契约即阻断（T09）。 |
| G9 | 金标准数据 | 手工可核对合成数据集（3 门店 × 3 品类 × 两期），其中一组的品类合计数值等于 proposal 4.5 示例（500k/300k/200k → -130k）；期望结果含各结果表数值与全部检查状态（输入—预期结果—预期检查三元组）。失败样例从 T01—T24 逐条映射。理由：proposal M0/14.1。 |
| G10 | git 仓库 | IMPLEMENT 开始时对项目目录 `git init`（本地仓库、无 remote、不推送），`.flow/state.json` 入 `.gitignore`，先提交基线（.flow 工件）作为 review_base。理由：非 git 仓库会使 REVIEW 降级跳过；git init 可逆、无破坏性，是流程基础设施。 |
| G11 | UI 语言与格式 | 界面文案 zh-CN；日期/金额格式遵循项目配置（G8）。 |
| G12 | 依赖版本锁定 | DuckDB、jsonschema 等版本在 M0 锁定（uv lock），版本号写入方法注册表与执行清单。理由：proposal 12"技术实现版本由工程启动时锁定"。 |

