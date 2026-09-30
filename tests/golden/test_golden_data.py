"""金标准数据完整性：用 stdlib csv 独立复核期望结果（不经过未来计算引擎）。

期望值是手算字面量；品类合计锚定 proposal 4.5 示例数值。
此测试保护的是数据↔期望的一致性，S5 的引擎测试再与这里对齐。
"""

import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent

BASE_MONTH = "2026-07"
REPORT_MONTH = "2026-08"


def _load_rows() -> list[dict[str, str]]:
    with (GOLDEN_DIR / "sales-golden.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _load_expected() -> dict:
    return json.loads((GOLDEN_DIR / "expected-results.json").read_text(encoding="utf-8"))


def _sum_by(rows: list[dict[str, str]], month: str, key: str) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["business_date"].startswith(month):
            totals[r[key]] += int(r["net_sales_amount"])
    return dict(totals)


class TestGoldenDataMatchesExpectations:
    def test_row_count_and_uniqueness(self) -> None:
        rows = _load_rows()
        expected = _load_expected()
        ids = [r["row_id"] for r in rows]
        assert len(rows) == expected["quality"]["row_count"]
        assert len(set(ids)) == len(ids)

    def test_all_dates_parse_and_single_currency(self) -> None:
        rows = _load_rows()
        for r in rows:
            date.fromisoformat(r["business_date"])
            assert r["currency"] == "CNY"
            assert int(r["net_sales_amount"]) > 0

    def test_overall_totals_match_hand_literals(self) -> None:
        rows = _load_rows()
        expected = _load_expected()
        base = sum(
            int(r["net_sales_amount"])
            for r in rows
            if r["business_date"].startswith(BASE_MONTH)
        )
        report = sum(
            int(r["net_sales_amount"])
            for r in rows
            if r["business_date"].startswith(REPORT_MONTH)
        )
        assert base == expected["overall"]["base_amount"] == 1_000_000
        assert report == expected["overall"]["report_amount"] == 870_000
        assert report - base == expected["overall"]["delta"] == -130_000

    def test_store_slices_match(self) -> None:
        rows = _load_rows()
        expected = _load_expected()["by_store"]
        base = _sum_by(rows, BASE_MONTH, "store_id")
        report = _sum_by(rows, REPORT_MONTH, "store_id")
        for store in ("S01", "S02", "S03"):
            assert base[store] == expected[store]["base_amount"]
            assert report[store] == expected[store]["report_amount"]
            assert report[store] - base[store] == expected[store]["delta"]

    def test_category_slices_match_proposal_example(self) -> None:
        # proposal 4.5：女装 -120,000 / 男装 -30,000 / 配饰 +20,000，合计 -130,000
        rows = _load_rows()
        expected = _load_expected()["by_category"]
        base = _sum_by(rows, BASE_MONTH, "category_id")
        report = _sum_by(rows, REPORT_MONTH, "category_id")
        assert (base["C01"], report["C01"]) == (500_000, 380_000)
        assert report["C01"] - base["C01"] == -120_000
        assert (base["C02"], report["C02"]) == (300_000, 270_000)
        assert report["C02"] - base["C02"] == -30_000
        assert (base["C03"], report["C03"]) == (200_000, 220_000)
        assert report["C03"] - base["C03"] == 20_000
        for cat in ("C01", "C02", "C03"):
            assert expected[cat]["delta"] == report[cat] - base[cat]

    def test_slices_reconcile_to_overall(self) -> None:
        # proposal 4.3：门店与品类是同一整体差异的两个独立切片，各自对账回整体
        expected = _load_expected()
        store_delta_sum = sum(
            v["delta"] for v in expected["by_store"].values()
        )
        category_delta_sum = sum(
            v["delta"] for v in expected["by_category"].values()
        )
        assert store_delta_sum == category_delta_sum == expected["overall"]["delta"]
