# Analysis Plan Studio P0 — 纵向切片拆解

> 来源：`.flow/prd.md`（P0 范围 + GRILL 决议）。无 issue tracker，按 dev-flow 降级写入本文件。
> 拆解自批准（dev-flow 规则）：粒度 = 每片独立可验证、按 M0→M3 依赖序；不引入 PRD 之外的决策。

- [x] 1. 工程基座与契约内核（M0：Schema + 摘要 + 金标准）
- [x] 2. 计划服务与 G0 校验 + 项目存储
- [x] 3. 数据快照与语义绑定
- [ ] 4. 确认与版本治理（G1）
- [ ] 5. 受限执行闭环（方法 + Runner + 预算）
- [ ] 6. 证据与验收（G3）+ M1 端到端闭环
- [ ] 7. 模板与导出（G4）
- [ ] 8. HTTP 服务层
- [ ] 9. 五页面 UI（M2）
- [ ] 10. 失败路径矩阵与验收收口（M3：T01—T24）

---

## 1. 工程基座与契约内核（M0）

### What to build
项目从零起步：git init、uv 工程（Python 3.12+）、pytest/ruff/mypy 配置；`contracts/` 下 12 类核心对象的 JSON Schema（Draft 2020-12）最小可用集；JCS(RFC 8785)+SHA-256 摘要模块与跨语言风格测试向量；金标准合成数据集（含 proposal 4.5 数值用例）与期望结果三元组；T01—T24 → 失败样例的映射骨架。本片完成时"契约先行"落地：任何后续实现只消费这些契约。

### Acceptance criteria
- [ ] proposal 附录 B 结构的计划样例通过 `analysis-plan-ir` Schema 校验
- [ ] 摘要模块对测试向量产出稳定 digest；键序/数值表示规范化有负例证明
- [ ] Schema 负例被拒：错误日期 format、未知枚举、额外字段、坏引用
- [ ] 金标准数据集与期望结果文件就位且被测试加载
- [ ] `uv run pytest` 全绿（本片测试即先例）

### Blocked by
None - can start immediately

---

## 2. 计划服务与 G0 校验 + 项目存储

### What to build
`PlanService.create_draft / validate`：从模板实例化计划草稿并持久化；G0 门禁检查（结构、必填、引用存在、依赖无环、方法受支持、引用禁 latest）；`ValidationReport` 可定位到字段/步骤；计划状态轴（DRAFT/NEEDS_INPUT/READY_FOR_REVIEW/…）与版本号；ProjectStore（SQLite 索引 + 文件存储 + 暂存发布）。CLI 测试入口可创建并校验草稿。

### Acceptance criteria
- [ ] 草稿创建 → 持久化 → 重新加载内容一致
- [ ] 缺失引用/循环依赖/UNSUPPORTED_METHOD 被定位报告（T22）
- [ ] 状态轴流转正确；历史版本不被覆盖
- [ ] 全部断言走服务接口（进程内），不测内部函数

### Blocked by
- 1. 工程基座与契约内核

---

## 3. 数据快照与语义绑定

### What to build
导入本地 CSV/Parquet → 复制为项目快照 → 字节 SHA-256 指纹 + 解析配置入 `DataSnapshotManifest`；`ContextPort.bind`：字段映射（六约定字段）、指标契约（税/优惠/退款口径）、比较范围（基期/报告期/门店资格清单或全量标注）；项目口径配置（单币种、时区显式确认、金额精度）。

### Acceptance criteria
- [ ] 金标准 CSV 导入后 manifest 完整、指纹稳定；外部改原文件不影响快照（T11 后半）
- [ ] 字段缺失被定位阻断（T04）；混合币种阻断（T09）
- [ ] 无门店资格清单时比较范围标注"全量观察范围"，不出现同店表述（T08）
- [ ] 覆盖清单缺失 → WARN + 用户显式确认后才可继续（G4 决议）

### Blocked by
- 2. 计划服务与 G0 校验 + 项目存储

---

## 4. 确认与版本治理（G1）

### What to build
`ApprovalService.approve`：approval_target 覆盖六类对象版本与 digest；确认记录独立保存、含 user_action 上下文（来源 cli/ui/test）；`PlanService.diff` 语义级差异（旧值/新值/影响对象）；`ChangeRequest` 提议与合入；实质变更 → 新版本 + 旧确认失效；标题/备注 → 注释记录。

### Acceptance criteria
- [ ] 确认记录包含全部六类 digest 且可复算验证
- [ ] 确认后修改期间/过滤/指标/方法 → 旧确认失效、须重新确认（T10）
- [ ] diff 输出语义差异而非字符串差异
- [ ] 服务自身无法在没有 user_action 时生成有效确认

### Blocked by
- 3. 数据快照与语义绑定

---

## 5. 受限执行闭环（方法 + Runner + 预算）

### What to build
`methods/` 固定方法集（quality/scope/period-delta/group-delta/reconcile）与检查集（quality-and-coverage/group-reconciliation/evidence-coverage），版本锁定；IR → ExecutionManifest 受限编译（类型化过滤操作符，无自由表达式）；Run Controller 状态机（QUEUED/PREFLIGHT/BLOCKED/RUNNING/COMPLETED/FAILED/CANCELLED/TIMED_OUT）+ 预算默认值（G7 决议）；独立 Worker 子进程（JSON-lines、路径允许清单、RLIMIT、进程终止取消）；执行回执。数值规则：基期≤0 不出增长率、总变化近零不用 Δg/Δ 排名、切片不相加（T23）、定点精确对账。

### Acceptance criteria
- [ ] 金标准数据六步执行，结果表数值与期望三元组完全一致（T02）
- [ ] 重复 row_id → 检查失败阻断，不静默去重（T05）
- [ ] 超时/取消 → 后续步骤停止、部分证据保留、无成功标记（T16）
- [ ] 快照指纹/方法版本不符 → 阻断（T11/T12）
- [ ] Worker 拒绝允许清单外路径与未知操作符（T15）

### Blocked by
- 4. 确认与版本治理（G1）

---

## 6. 证据与验收（G3）+ M1 端到端闭环

### What to build
`EvidencePort.collect / validate`：证据最小结构（evidence_id/run_id/plan_digest/step_id/指纹/验证状态）；G3 验收（对账、覆盖、证据完整性）；Finding 生成器（仅 descriptive / arithmetic-decomposition，因果文本入待验证区）；ReviewRecord（接受/带限制接受/退回）与三状态轴分离；复跑生成新 run_id。M1 收口：一条 E2E 测试走完 草稿→绑定→确认→执行→证据→验收→复跑。

### Acceptance criteria
- [ ] 每条数值发现可追溯至本次运行证据；跨运行证据被拒（T17）
- [ ] 人为篡改分组结果 → 验证失败，不得完整接受（T14）
- [ ] 必需检查 UNKNOWN → 阻断一切接受（G4 决议；T07）
- [ ] "断货导致下降"类草稿文本入待验证区，不入结论（T18）
- [ ] 运行完成 ≠ 验证通过 ≠ 用户接受，三者分轴存储（T06 场景同时覆盖）

### Blocked by
- 5. 受限执行闭环

---

## 7. 模板与导出（G4）

### What to build
`TemplateService.extract / instantiate`：提取方法/参数槽位/验证规则，清除旧批准、旧数据、旧结论；`ExportService.build_package`：计划交接包（默认无原始数据/无绝对路径）、分析交付包、本地复现包；导入核验（版本、大小、引用、摘要；拒绝路径穿越与携带代码）。

### Acceptance criteria
- [ ] 模板实例化产物不含旧 run_id/批准/结果（T19）；换周期数据重新绑定后独立运行
- [ ] 交接包默认不含原始数据（T20）；含越界路径的异常包导入被拒并定位
- [ ] 同数据复跑模板生成新 run_id，不覆盖历史

### Blocked by
- 6. 证据与验收（G3）+ M1 端到端闭环

---

## 8. HTTP 服务层

### What to build
FastAPI 本地服务：仅回环绑定、会话令牌、来源检查；九个服务接口的 REST 包装（同一进程内边界，规则不复制）；OpenAPI 导出 → TypeScript 类型生成入前端工程。

### Acceptance criteria
- [ ] 无令牌/跨来源请求被拒（proposal 10.5）
- [ ] 九服务各有端点冒烟测试；业务断言不在 HTTP 层重复
- [ ] OpenAPI → TS 类型生成可重复执行

### Blocked by
- 7. 模板与导出（G4）

---

## 9. 五页面 UI（M2）

### What to build
TypeScript + React + Vite：项目与模板 / 分析计划 / 数据与口径 / 检查与确认 / 运行与验收 五页面 + JSON/IR 高级视图；视觉基线遵循全局 DESIGN.md；所有按钮调用 S8 的共享服务；业务版计划卡片与 IR 同源渲染。

### Acceptance criteria
- [ ] proposal 5.1 五页面各自的完成条件可走通
- [ ] 修改正式 IR 范围后重渲染，用户所见与机器所用一致（T21）
- [ ] 无数据时显示"未绑定/不可执行"；失败/未知检查不显示为通过
- [ ] 关键路径（创建计划→确认→运行→验收）UI 冒烟通过

### Blocked by
- 8. HTTP 服务层

---

## 10. 失败路径矩阵与验收收口（M3）

### What to build
T01—T24 全量映射核对：已自动化项补齐断言，UI 相关项做冒烟；交付成功/失败/受限三类状态样例项目；性能测试点（10 万/100 万行）实测记录；上线硬门槛三件事的演示证据（正常分析、关键失败阻断、跨周期复用）。

### Acceptance criteria
- [ ] T01—T24 每条有自动化测试或明确记录的验证方式与结果
- [ ] 三类状态样例项目可加载演示
- [ ] 性能记录（耗时/内存/临时空间）如实写入文档，含实测环境说明
- [ ] T01（无平台依赖）与 T24（断网）路径验证通过

### Blocked by
- 9. 五页面 UI（M2）
