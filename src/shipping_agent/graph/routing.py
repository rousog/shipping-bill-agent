"""条件边函数（Phase 0 占位）。

后续实现：
- route_validate: hard -> review / retry -> extract / pass -> match
- route_match: 0 -> create / 1 -> decide / >1 -> review
- route_decision: update / no_change / pending -> review
"""

from __future__ import annotations

from .state import AgentState


def route_validate(state: AgentState) -> str:
    hard_issues = [i for i in state.get("issues", []) if i.severity == "hard"]
    if hard_issues:
        return "review"
    return "match"
