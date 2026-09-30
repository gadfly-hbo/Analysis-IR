# Analysis Plan Studio（Analysis-IR 项目）

> 先确认 AI 准备怎么分析，再检查它实际做了什么，并把这套方法留给下一次复用。
> 事实源：`.flow/proposal.md`（产品方案 v1.0）> `.flow/prd.md`（P0 PRD + GRILL 决议）。

本地优先、单场景（销售差异与多维贡献拆解）的分析计划与验证工具。
P0 闭环：**提出问题 → 明确约定 → 检查并确认 → 受限执行 → 证据验收 → 保存与复用**。

## 运行

```bash
uv sync                              # Python 依赖（版本锁定见 uv.lock）
uv run python -m app <项目目录>       # 启动本地服务 → http://127.0.0.1:8765
# 前端开发模式：cd app/web && npm install && npm run dev（代理 /api → 8765）
# 前端构建（由本地服务托管）：cd app/web && npm run build
```

CLI（M1 测试入口）：

```bash
uv run python -m app.cli create-draft --db proj.db --question "…" --purpose "…" \
  --base-start 2026-07-01 --base-end 2026-07-31 \
  --report-start 2026-08-01 --report-end 2026-08-31 --operator analyst-a
uv run python -m app.cli validate --db proj.db --plan <plan_id>
```

## 结构（proposal 10.2）

```
contracts/    12 类对象的 JSON Schema（Draft 2020-12）——唯一契约权威
toolkit/      计划/校验/确认/差异/模板/执行/证据/发现（不含 UI 逻辑）
adapters/     local_context（绑定）/ local_store（SQLite）
methods/      版本锁定的固定方法与检查 + 受限 Worker 子进程
app/          FastAPI 本地服务（回环+令牌）+ CLI + web/（React 五页面）
tests/        golden（金标准）/ failures（T 矩阵）/ conformance（摘要向量）
scripts/      perf_check.py（性能记录）
docs/         performance.md
```

## 验证

```bash
uv run pytest          # 110 项测试（含 T01—T24 验收矩阵，见 tests/failures/T-MAP.md）
uv run ruff check .
uv run mypy
cd app/web && npm run build
uv run python scripts/perf_check.py 100000   # 性能记录 → docs/performance.md
```

## 边界（P0 明确不做）

任意生成 SQL/Python 执行、LLM 集成（P1）、JuanerAI/Xanthil 对接（P2）、
企业权限、多租户、断点续跑。外部 Agent 仅可导入结构化草稿（经 Schema 负例校验，夹带字段被拒绝，T15；专用导入入口属 P1）。
已知限制（如实披露，P0 范围内未实现或不完整）：
- 取消运行为同步模型的声明式接口（超时/中断路径已强制）；`step_timeout_seconds` 暂无独立执行点，以 run 级超时兜底
- RLIMIT_FSIZE 语义是单文件大小上限而非聚合临时空间；macOS 上 RLIMIT_AS 可能不被内核强制（Linux 行为未实测）
- 验证状态仅产生 PASSED/FAILED；WARN/LIMITED/NOT_APPLICABLE 为 schema 预留，带限制接受当前仅在 PASSED 后可用
- G8 的时区/币种"首次显式确认"未落地（时区固定 Asia/Shanghai、无币种列默认 CNY，混合币种仍阻断）
- F12 专用草稿导入入口属 P1（当前经 Schema 负例校验拒绝夹带字段）
- 覆盖 ack 状态不持久化（刷新后需回数据页重新确认警告）
