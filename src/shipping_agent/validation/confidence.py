"""置信度计算。"""

from __future__ import annotations

from ..models import ValidationIssue


def score_confidence(issues: list[ValidationIssue]) -> float:
    hard = sum(1 for issue in issues if issue.severity == "hard")
    soft = sum(1 for issue in issues if issue.severity == "soft")
    value = 1.0 - 0.3 * hard - 0.1 * soft
    return max(0.0, min(1.0, round(value, 4)))
