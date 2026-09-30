"""校验报告模型（G0 门禁的输出结构）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ValidationIssue:
    code: str  # 如 MISSING_REFERENCE / UNSUPPORTED_METHOD / CYCLIC_DEPENDENCY
    location: str  # 可定位到字段/步骤，如 "steps[2]" 或 "metric_refs[0]"
    message: str


@dataclass(frozen=True)
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def g0_passed(self) -> bool:
        return not self.issues
