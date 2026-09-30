# T01—T24 → 失败样例映射骨架

> proposal 14.1 验收矩阵到测试夹具/切片的映射。状态：`planned`（骨架，S2 起逐片补齐）／`covered`（已有自动化）。

| 编号 | 场景 | 归属切片 | 夹具/测试位置（计划） | 状态 |
|---|---|---|---|---|
| T01 | 独立启动（无平台/模型） | S10 | tests/failures/test_t01_standalone.py | covered → tests/test_t_matrix.py::TestOfflineStandalone |
| T02 | 正常分析金标准 | S5 | tests/golden/test_engine_golden.py | covered → tests/test_runner.py::TestGoldenRun |
| T03 | 契约歧义（退款/范围未说明） | S2/S3 | tests/failures/test_t03_contract_ambiguity.py | covered → tests/test_approval.py::TestApprovalBlockers + tests/test_plan_service.py |
| T04 | 必需字段缺失 | S3 | tests/failures/test_t04_missing_field.py | covered → tests/test_context_binding.py::TestBindFailures |
| T05 | 重复 row_id | S5 | tests/failures/test_t05_duplicate_rows.py | covered → tests/test_runner.py::TestExecutionBlocks |
| T06 | 零/负基数 | S5 | tests/failures/test_t06_nonpositive_base.py | covered → tests/test_runner.py::TestExecutionBlocks |
| T07 | 覆盖缺口 | S3/S6 | tests/failures/test_t07_coverage_gap.py | covered → tests/test_runner.py + tests/test_evidence.py |
| T08 | 同店资格不足 | S3 | tests/failures/test_t08_same_store_eligibility.py | covered → tests/test_context_binding.py::TestScopeEligibility |
| T09 | 不可加总口径（混合币种） | S3 | tests/failures/test_t09_mixed_currency.py | covered → tests/test_context_binding.py::TestBindFailures |
| T10 | 计划变化致旧确认失效 | S4 | tests/failures/test_t10_plan_change.py | covered → tests/test_approval.py::TestVersionGovernance |
| T11 | 快照变化/指纹不符 | S3/S5 | tests/failures/test_t11_snapshot_tamper.py | covered → tests/test_context_binding.py + test_approval.py + test_runner.py |
| T12 | 方法/规则版本变化 | S5 | tests/failures/test_t12_version_mismatch.py | covered → tests/test_runner.py::TestExecutionBlocks |
| T13 | 缺检查实现→UNKNOWN | S6 | tests/failures/test_t13_missing_check.py | covered → tests/test_t_matrix.py::TestMissingCheckImplementation |
| T14 | 错误对账 | S6 | tests/failures/test_t14_bad_reconciliation.py | covered → tests/test_evidence.py |
| T15 | 越界执行（自由 SQL/远程路径/未知操作符） | S5 | tests/failures/test_t15_out_of_bounds.py | covered → tests/test_runner.py::TestWorkerProtocol + S1 schema 负例 |
| T16 | 超时/取消/崩溃 | S5 | tests/failures/test_t16_interruption.py | covered → tests/test_runner.py::TestWorkerProtocol + test_evidence.py |
| T17 | 跨运行伪回执 | S6 | tests/failures/test_t17_stale_evidence.py | covered → tests/test_evidence.py |
| T18 | 无证据的因果结论 | S6 | tests/failures/test_t18_unevidenced_cause.py | covered → tests/test_evidence.py::TestFindings |
| T19 | 模板复用清除旧状态 | S7 | tests/failures/test_t19_template_reuse.py | covered → tests/test_template_export.py |
| T20 | 导出/导入异常包 | S7 | tests/failures/test_t20_export_import.py | covered → tests/test_template_export.py |
| T21 | 同源可读性 | S9 | tests/failures/test_t21_same_source_render.py（UI 冒烟） | covered（服务级同源）→ tests/test_http_layer.py::test_same_source_readability + UI 冒烟 app/web/src/smoke.test.tsx |
| T22 | 不支持的计划（环/坏引用/未知版本） | S2 | tests/failures/test_t22_unsupported_plan.py | covered → tests/test_plan_service.py::TestValidate |
| T23 | 错误维度合计（切片相加） | S5 | tests/failures/test_t23_dimension_double_count.py | covered → tests/test_runner.py::TestExecutionBlocks |
| T24 | 无模型/断网 | S10 | tests/failures/test_t24_offline.py | covered → tests/test_t_matrix.py::TestOfflineStandalone |
