# REVIEW R2 发现（2026-09-30）

**总判定：FAIL**（0 Blocker + 3 Major + 8 Minor）。R1 修复主体有效（B1/M2/M4/M5 已修复，M3/M6 部分）；验证命令重跑一致。完整报告见会话记录，要点：

## Major（本轮修复）
- R2-1 CR UI 恒 500：submitChange 硬编码 old/new "?" → merge 端 ContractViolation 穿透为 500；无新值输入；propose+merge 一步违反 5.3（应先展示 CR）。
- R2-2 schema-invalid 契约可获批准：`base_period.start="not-a-date"` 过 G0 并 APPROVED（_scope_issues 只查 truthiness；approve 不校验 contract Schema）。
- R2-3 test_controller_blocks_scopeless_plan_defensively 名实不符（实为正常路径回归；防御分支零覆盖）。

## Minor（本轮修复/披露）
- step_timeout 无执行点 + FSIZE 是单文件上限非聚合空间 → README 披露
- _progress_from_stdout 丢弃 worker step error → 修复
- LIMITED/WARN 死状态 + G8 时区/币种显式确认未实现 → README 披露
- bind 空币种串='' 判 CNY 与 worker FAIL 口径不一致 → 对齐（都忽略空串）
- to-verify 空 text / changes ContractViolation → 4xx
- 资格清单 rule_note UI 输入 → 补
- test_review_fixes 晦涩断言 + __import__ → 清理；README 97→101
- Linux RLIMIT_AS × jemalloc 未验证 → README 披露
