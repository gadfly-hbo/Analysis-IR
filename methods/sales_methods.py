"""销售差异与多维贡献拆解：版本锁定的固定方法实现（proposal 第 4 章）。

所有 SQL 由方法实现按类型化参数生成，不存在用户可控表达式（9.1）。
金额以 DECIMAL(18,2) 读入、以"分"（整数）参与对账——定点精确比较（4.4）。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

SALES_METHODS: dict[str, str] = {
    "quality": "1.0.0",
    "scope": "1.0.0",
    "period-delta": "1.0.0",
    "group-delta": "1.0.0",
    "reconcile": "1.0.0",
}

_CSV_COLUMNS = {
    "row_id": "VARCHAR",
    "business_date": "DATE",
    "store_id": "VARCHAR",
    "category_id": "VARCHAR",
    "net_sales_amount": "DECIMAL(18,2)",
    "currency": "VARCHAR",
}


def load_snapshot(conn: duckdb.DuckDBPyConnection, snapshot_path: str) -> None:
    conn.execute(
        """
        CREATE TABLE sales AS SELECT * FROM read_csv(?, header=true,
            columns = {'row_id': 'VARCHAR', 'business_date': 'DATE',
                       'store_id': 'VARCHAR', 'category_id': 'VARCHAR',
                       'net_sales_amount': 'DECIMAL(18,2)', 'currency': 'VARCHAR'},
            dateformat = '%Y-%m-%d')
        """,
        [snapshot_path],
    )


def _cents(value: Any) -> int:
    return int((Decimal(str(value)) * 100).to_integral_value())


def _yuan(cents: int) -> float:
    return cents / 100.0


def _scalar(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> Any:
    row = conn.execute(sql, params or []).fetchone()
    assert row is not None, f"查询无结果: {sql[:60]}"
    return row[0]


class StepFailure(Exception):
    """步骤业务失败（如质量检查 FAIL 且失败动作为 block-step）。"""


def run_quality(
    conn: duckdb.DuckDBPyConnection,
    scope: dict[str, Any],
    coverage: dict[str, Any],
    metric_currency: str,
) -> dict[str, Any]:
    """S01 数据质量检查 → quality-evidence。"""
    total = _scalar(conn, "SELECT COUNT(*) FROM sales")
    distinct_ids = _scalar(conn, "SELECT COUNT(DISTINCT row_id) FROM sales")
    duplicates = total - distinct_ids
    null_keys = _scalar(
        conn,
        "SELECT COUNT(*) FROM sales WHERE row_id IS NULL OR business_date IS NULL "
        "OR store_id IS NULL OR category_id IS NULL OR net_sales_amount IS NULL",
    )
    currencies = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT currency FROM sales "
            "WHERE currency IS NOT NULL AND currency <> '' ORDER BY 1"
        ).fetchall()
    ]

    observed_dates = {
        str(r[0])
        for r in conn.execute(
            "SELECT DISTINCT CAST(business_date AS DATE) FROM sales ORDER BY 1"
        ).fetchall()
    }
    coverage_status, coverage_detail = _coverage_status(coverage, scope, observed_dates)

    checks = [
        {"name": "row-uniqueness", "status": "PASS" if duplicates == 0 else "FAIL",
         "detail": f"{duplicates} 条重复 row_id（不可静默去重）"},
        {"name": "key-fields-not-null", "status": "PASS" if null_keys == 0 else "FAIL",
         "detail": f"{null_keys} 行关键字段为空"},
        {"name": "single-currency",
         "status": "PASS" if currencies == [metric_currency] else "FAIL",
         "detail": f"币种: {currencies}（契约 {metric_currency}）"},
        {"name": "coverage", "status": coverage_status, "detail": coverage_detail},
    ]
    failed = [c for c in checks if c["status"] == "FAIL"]
    overall = "FAIL" if failed else ("UNKNOWN" if coverage_status == "UNKNOWN" else "PASS")
    return {
        "kind": "check-record",
        "check": "sales-checks:quality-and-coverage@1.0.0",
        "row_count": total,
        "checks": checks,
        "status": overall,
        "failure_action": "block-step" if failed else "block-acceptance-if-unknown",
    }


def _coverage_status(
    coverage: dict[str, Any], scope: dict[str, Any], observed: set[str]
) -> tuple[str, str]:
    kind = coverage.get("kind", "unverified")
    if kind == "unverified":
        return "UNKNOWN", "无覆盖证明；不把缺记录当零销售（T07）"
    if kind == "manifest-ref":
        declared = {str(d) for d in coverage.get("dates", [])}
        if observed == declared:
            return "PASS", f"观察到的 {len(observed)} 个日期与覆盖清单一致"
        missing = sorted(declared - observed)[:5]
        extra = sorted(observed - declared)[:5]
        return "FAIL", f"观察日期与清单不符: 缺 {missing}, 多 {extra}"
    # declared-complete：期间内每个日历日都应有记录
    expected = _calendar_dates(scope)
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    if not missing and not extra:
        return "PASS", f"期间 {len(expected)} 个日历日全部覆盖"
    return "FAIL", f"声明完整但缺 {missing[:5]} 个日历日" + (f"，多出 {extra[:5]}" if extra else "")


def _calendar_dates(scope: dict[str, Any]) -> set[str]:
    dates: set[str] = set()
    for key in ("base", "report"):
        period = scope[key]
        start = date.fromisoformat(period["start"])
        end = date.fromisoformat(period["end"])
        while start <= end:
            dates.add(start.isoformat())
            start += timedelta(days=1)
    return dates


def run_scope(
    conn: duckdb.DuckDBPyConnection, scope: dict[str, Any], eligible_stores: list[str] | None
) -> dict[str, Any]:
    """S02 构建比较范围 → scoped-sales 证据（过滤后的行留在 sales 表内原地视图）。"""
    base_start, base_end = scope["base"]["start"], scope["base"]["end"]
    report_start, report_end = scope["report"]["start"], scope["report"]["end"]
    rows_total = _scalar(conn, "SELECT COUNT(*) FROM sales")
    amount_total = _scalar(
        conn, "SELECT COALESCE(SUM(net_sales_amount), 0) FROM sales"
    )
    conn.execute(
        "CREATE OR REPLACE TABLE scoped AS SELECT * FROM sales WHERE "
        "(business_date BETWEEN ? AND ?) OR (business_date BETWEEN ? AND ?)",
        [base_start, base_end, report_start, report_end],
    )
    if eligible_stores is not None:
        conn.execute(
            "DELETE FROM scoped WHERE store_id NOT IN (SELECT unnest(?::VARCHAR[]))",
            [eligible_stores],
        )
    rows_scoped = _scalar(conn, "SELECT COUNT(*) FROM scoped")
    amount_scoped = _scalar(
        conn, "SELECT COALESCE(SUM(net_sales_amount), 0) FROM scoped"
    )
    return {
        "kind": "method-output",
        "rows_before": rows_total,
        "rows_after": rows_scoped,
        "amount_before_cents": _cents(amount_total),
        "amount_after_cents": _cents(amount_scoped),
        "store_scope": "declared-list" if eligible_stores is not None else "all-stores",
    }


def _period_sum(conn: duckdb.DuckDBPyConnection, start: str, end: str) -> int:
    value = _scalar(
        conn,
        "SELECT COALESCE(SUM(net_sales_amount), 0) FROM scoped "
        "WHERE business_date BETWEEN ? AND ?",
        [start, end],
    )
    return _cents(value)


def run_period_delta(conn: duckdb.DuckDBPyConnection, scope: dict[str, Any]) -> dict[str, Any]:
    """S03 整体差异 → overall-delta。基期 ≤ 0 不输出普通增长率（4.4）。"""
    base = _period_sum(conn, scope["base"]["start"], scope["base"]["end"])
    report = _period_sum(conn, scope["report"]["start"], scope["report"]["end"])
    delta = report - base
    rate = round(delta / base, 6) if base > 0 else None
    return {
        "kind": "method-output",
        "base_amount_cents": base,
        "report_amount_cents": report,
        "delta_cents": delta,
        "rate": rate,
        "rate_note": None if base > 0 else "基期金额 ≤ 0，仅展示绝对变化",
    }


def run_group_delta(
    conn: duckdb.DuckDBPyConnection, scope: dict[str, Any], dimension: str
) -> dict[str, Any]:
    """S04/S05 分组拆解 → 分组贡献表（独立切片，不与另一维度相加，T23）。"""
    if dimension not in ("store_id", "category_id"):
        raise ValueError(f"不支持的分组维度: {dimension}")
    rows = conn.execute(
        f"""
        SELECT {dimension}, 
               COALESCE(SUM(net_sales_amount) FILTER (
                   WHERE business_date BETWEEN ? AND ?), 0) AS base_amount,
               COALESCE(SUM(net_sales_amount) FILTER (
                   WHERE business_date BETWEEN ? AND ?), 0) AS report_amount
        FROM scoped GROUP BY {dimension} ORDER BY {dimension}
        """,  # dim 已被白名单校验，非用户输入
        [scope["base"]["start"], scope["base"]["end"],
         scope["report"]["start"], scope["report"]["end"]],
    ).fetchall()
    groups = []
    for value, base_amount, report_amount in rows:
        base_c, report_c = _cents(base_amount), _cents(report_amount)
        delta = report_c - base_c
        groups.append({
            "dimension": dimension,
            "value": value,
            "base_amount_cents": base_c,
            "report_amount_cents": report_c,
            "delta_cents": delta,
            "rate": round(delta / base_c, 6) if base_c > 0 else None,
        })
    return {
        "kind": "method-output",
        "dimension": dimension,
        "groups": groups,
        "slice_note": f"{dimension} 是独立切片，不得与其他维度贡献相加（T23）",
    }


def run_reconcile(
    conn: duckdb.DuckDBPyConnection,
    scope: dict[str, Any],
    overall: dict[str, Any],
    store_delta: dict[str, Any],
    category_delta: dict[str, Any],
) -> dict[str, Any]:
    """S06 对账：各切片 Δg 之和 = 整体 Δ（定点精确比较，无容差）。"""
    overall_delta = overall["delta_cents"]
    slices = []
    for name, artifact in (("store_id", store_delta), ("category_id", category_delta)):
        ssum = sum(g["delta_cents"] for g in artifact["groups"])
        slices.append({
            "dimension": name,
            "group_delta_sum_cents": ssum,
            "overall_delta_cents": overall_delta,
            "status": "PASS" if ssum == overall_delta else "FAIL",
        })
    limitations = []
    for artifact in (store_delta, category_delta):
        zero_base = [g["value"] for g in artifact["groups"] if g["rate"] is None]
        if zero_base:
            limitations.append(
                f"{artifact['dimension']} 基期为零/负: {zero_base}，仅展示绝对变化"
            )
    overall_status = "PASS" if all(s["status"] == "PASS" for s in slices) else "FAIL"
    return {
        "kind": "check-record",
        "check": "sales-checks:group-reconciliation@1.0.0",
        "slices": slices,
        "status": overall_status,
        "limitations": limitations,
        "failure_action": "block-acceptance" if overall_status == "FAIL" else "none",
    }


def write_artifact(run_dir: Path, name: str, payload: dict[str, Any]) -> Path:
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    path = artifacts / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
