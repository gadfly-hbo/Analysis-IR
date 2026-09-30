"""M1 测试入口 CLI：创建草稿、校验计划（G0）。

薄壳：只做参数解析与 JSON 输出，全部业务走 PlanService。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from adapters.local_store.project_store import ProjectStore
from toolkit.plan_service import PlanService


def _service(db: Path) -> PlanService:
    return PlanService(ProjectStore(db))


def cmd_create_draft(args: argparse.Namespace) -> int:
    svc = _service(Path(args.db))
    svc.register_builtin_registries()
    result = svc.create_draft(
        "sales-delta",
        {
            "question": args.question,
            "decision_purpose": args.purpose,
            "base_period": {"start": args.base_start, "end": args.base_end},
            "report_period": {"start": args.report_start, "end": args.report_end},
        },
        operator=args.operator,
    )
    print(
        json.dumps(
            {
                "plan_id": result.plan["plan_id"],
                "plan_version": result.plan["plan_version"],
                "contract_id": result.contract["id"],
                "unresolved": result.unresolved,
                "status": svc.get_status(result.plan["plan_id"], 1),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    svc = _service(Path(args.db))
    version = args.version or svc.store.latest_version("analysis-plan-ir", args.plan) or 1
    report = svc.validate(args.plan, version)
    print(
        json.dumps(
            {
                "plan": f"{args.plan}@{version}",
                "g0_passed": report.g0_passed,
                "status": svc.get_status(args.plan, version),
                "issues": [
                    {"code": i.code, "location": i.location, "message": i.message}
                    for i in report.issues
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.g0_passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aps")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create-draft", help="从内置模板创建计划草稿")
    p_create.add_argument("--db", required=True)
    p_create.add_argument("--question", required=True)
    p_create.add_argument("--purpose", required=True)
    p_create.add_argument("--base-start", required=True)
    p_create.add_argument("--base-end", required=True)
    p_create.add_argument("--report-start", required=True)
    p_create.add_argument("--report-end", required=True)
    p_create.add_argument("--operator", required=True)
    p_create.set_defaults(func=cmd_create_draft)

    p_validate = sub.add_parser("validate", help="对计划执行 G0 校验")
    p_validate.add_argument("--db", required=True)
    p_validate.add_argument("--plan", required=True)
    p_validate.add_argument("--version", type=int, default=None)
    p_validate.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
