# REVIEW R1 发现（独立 code-reviewer 子代理，2026-09-30）

**总判定：FAIL**（Blocker 1 + Major 5 + Minor 12）。验证命令重跑与冻结记录完全一致；digest 向量与金标准数值独立复核吻合。

## Blocker

**B1. T03"不说明比较范围"分支无门禁** — `toolkit/plan_service.py:124-127` 缺期间时 contract 不含 base/report_period；`instantiate_template` 不校验 contract schema；G0/approve 均无比较范围检查 → 缺期间计划可 APPROVED，`controller.start` KeyError('base_period')（HTTP 500）。实测已复现。归属 spec(T03)+task(S4)。复检：缺期间 → approve 应被 ApprovalRejected 拒绝。

## Major

**M2. UI 无法完成"覆盖未验证→确认"路径** — `app/web/src/api.ts:74-77` 硬编码空 ack；`main.tsx:307`"已知悉"按钮只跳页不传 ack → 服务端 409 死锁。归属 implementation/G4。
**M3. G7 预算只部分强制：无 RLIMIT，step 超时与临时空间纯声明** — 无 setrlimit；worker 仅设 DuckDB memory_limit；temp_space_mb 无执行点。归属 prd(G7)+task(S5)。
**M4. 验证 FAILED 时 UI 连退回都不可用；FAILED 运行部分证据无 UI 入口** — `main.tsx:444` 验收卡仅 PASSED 渲染；`main.tsx:413` 证据卡仅 COMPLETED 渲染。归属 spec(5.4/F08/T16)。
**M5. T18"待验证区"能力缺失且测试近同义反复** — 无产生 to-verify 类型的代码路径与录入入口；`test_evidence.py:117-127` 只断言生成器模板不含因果词。归属 task(S6)+spec(9.4)。
**M6. T21/UI 冒烟覆盖声明与交付不符；五页面关键操作缺口** — 无 UI 测试；T21 实为 API 同源性断言；diff 视图（F06/US17）、门店资格入口（G5/US12）、ChangeRequest 入口（5.3/US16）均缺；T-MAP 标 covered 过度声明。归属 task+verify。

## Minor（择要）

- single-currency 不与契约币种比对（sales_methods.py:100-102）
- G8 无币种列静默 CNY、时区硬编码（context_port.py:208-210 / execution.py:99-103）
- 证据项 method_or_check_version 硬编码 1.0.0（evidence_service.py:75）
- LIMITED/WARN/NOT_APPLICABLE 死状态值（evidence_service.py:40-42）
- cancel NotImplementedError（已声明偏离）
- GET findings 有副作用且 FAILED 运行会 500、allowed_types 硬编码（server.py:199-208）
- 导入不经 Schema 校验、无大小限制、不核对象引用（export_service.py:184-200）
- 性能记录缺临时空间（docs/performance.md）
- 发现 numbers 缺正向贡献金额绑定（findings.py:76-91）
- main.tsx:311 静态帮助文本含"同店"字样
- 工程卫生：tsbuildinfo 入库、LIKE 死代码、晦涩表达式、注释与断言矛盾
- F12 无专门导入入口（README 声明略强）

## 审查者确认

- 已检查全部 toolkit/methods/adapters/app/contracts/tests 模块；重跑四项验证命令与冻结记录一致；独立复核 digest 向量与金标准数值；在 /tmp 服务边界复现 B1。
- 未检查：浏览器手工 UI 操作（仅静态读码）；perf 未重跑；Schema 未逐字段全审；lock 文件未逐依赖核对。
