# T01—T24 → 失败样例映射骨架

> proposal 14.1 验收矩阵到测试夹具/切片的映射。状态：`planned`（骨架，S2 起逐片补齐）／`covered`（已有自动化）。

| 编号 | 场景 | 归属切片 | 夹具/测试位置（计划） | 状态 |
|---|---|---|---|---|
| T01 | 独立启动（无平台/模型） | S10 | tests/failures/test_t01_standalone.py | planned |
| T02 | 正常分析金标准 | S5 | tests/golden/test_engine_golden.py | planned |
| T03 | 契约歧义（退款/范围未说明） | S2/S3 | tests/failures/test_t03_contract_ambiguity.py | planned |
| T04 | 必需字段缺失 | S3 | tests/failures/test_t04_missing_field.py | planned |
| T05 | 重复 row_id | S5 | tests/failures/test_t05_duplicate_rows.py | planned |
| T06 | 零/负基数 | S5 | tests/failures/test_t06_nonpositive_base.py | planned |
| T07 | 覆盖缺口 | S3/S6 | tests/failures/test_t07_coverage_gap.py | planned |
| T08 | 同店资格不足 | S3 | tests/failures/test_t08_same_store_eligibility.py | planned |
| T09 | 不可加总口径（混合币种） | S3 | tests/failures/test_t09_mixed_currency.py | planned |
| T10 | 计划变化致旧确认失效 | S4 | tests/failures/test_t10_plan_change.py | planned |
| T11 | 快照变化/指纹不符 | S3/S5 | tests/failures/test_t11_snapshot_tamper.py | planned |
| T12 | 方法/规则版本变化 | S5 | tests/failures/test_t12_version_mismatch.py | planned |
| T13 | 缺检查实现→UNKNOWN | S6 | tests/failures/test_t13_missing_check.py | planned |
| T14 | 错误对账 | S6 | tests/failures/test_t14_bad_reconciliation.py | planned |
| T15 | 越界执行（自由 SQL/远程路径/未知操作符） | S5 | tests/failures/test_t15_out_of_bounds.py | planned |
| T16 | 超时/取消/崩溃 | S5 | tests/failures/test_t16_interruption.py | planned |
| T17 | 跨运行伪回执 | S6 | tests/failures/test_t17_stale_evidence.py | planned |
| T18 | 无证据的因果结论 | S6 | tests/failures/test_t18_unevidenced_cause.py | planned |
| T19 | 模板复用清除旧状态 | S7 | tests/failures/test_t19_template_reuse.py | planned |
| T20 | 导出/导入异常包 | S7 | tests/failures/test_t20_export_import.py | planned |
| T21 | 同源可读性 | S9 | tests/failures/test_t21_same_source_render.py（UI 冒烟） | planned |
| T22 | 不支持的计划（环/坏引用/未知版本） | S2 | tests/failures/test_t22_unsupported_plan.py | planned |
| T23 | 错误维度合计（切片相加） | S5 | tests/failures/test_t23_dimension_double_count.py | planned |
| T24 | 无模型/断网 | S10 | tests/failures/test_t24_offline.py | planned |
