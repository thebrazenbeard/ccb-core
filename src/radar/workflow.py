"""Radar workflow readiness and proportionate effect-scope rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Set


class WorkflowError(ValueError):
    pass


LANE_ACTION_REQUIRED_CREATE_LANE = "ACTION_REQUIRED:CREATE_LANE"
LANE_SATISFIED = "SATISFIED"
LANE_NOT_REQUIRED = "NOT_REQUIRED"

DECISION_REQUIRE_CLARIFICATION = "REQUIRE_CLARIFICATION"
DECISION_REQUIRE_EXACT_AUTHORITY = "REQUIRE_EXACT_AUTHORITY"
DECISION_DO_VERIFY_REPORT = "DO_VERIFY_REPORT"
DECISION_REQUIRE_ASSIGNMENT = "REQUIRE_ASSIGNMENT"


def _require_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise WorkflowError(f"INVALID_BOOLEAN:{name}")
    return value


def _mapping_bool(
    mapping: Mapping[str, Any],
    *keys: str,
    default: bool = False,
) -> bool:
    for key in keys:
        if key in mapping:
            return _require_bool(mapping[key], key)
    return default


def _assignment_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError("ASSIGNMENT_ID_REQUIRED")
    return value.strip()


@dataclass(frozen=True)
class EffectScope:
    isolated_branch: bool = False
    merge: bool = False
    deploy: bool = False

    def __post_init__(self) -> None:
        _require_bool(self.isolated_branch, "isolated_branch")
        _require_bool(self.merge, "merge")
        _require_bool(self.deploy, "deploy")

    def allows(self, action: str) -> bool:
        if action in {"create_isolated_branch", "create_branch", "write_isolated"}:
            return self.isolated_branch
        if action == "merge":
            return self.merge
        if action == "deploy":
            return self.deploy
        return False


def classify_lane_requirement(*, required: bool, exists: bool) -> str:
    required = _require_bool(required, "required")
    exists = _require_bool(exists, "exists")
    if required and not exists:
        return LANE_ACTION_REQUIRED_CREATE_LANE
    if required and exists:
        return LANE_SATISFIED
    return LANE_NOT_REQUIRED


def resolve_effect_scope(
    *, older: Mapping[str, Any], newer: Mapping[str, Any]
) -> EffectScope:
    if not isinstance(older, Mapping) or not isinstance(newer, Mapping):
        raise WorkflowError("EFFECT_SCOPE_MAPPING_REQUIRED")

    isolated = _mapping_bool(
        newer,
        "create_isolated_branch",
        "create_branch",
        "write_isolated",
        default=False,
    )
    if not isolated:
        local_only = _mapping_bool(older, "local_only", default=False)
        isolated = (not local_only) and _mapping_bool(
            older,
            "isolated_branch",
            "create_isolated_branch",
            default=False,
        )

    merge = _mapping_bool(
        newer,
        "merge",
        default=_mapping_bool(older, "merge", default=False),
    )
    deploy = _mapping_bool(
        newer,
        "deploy",
        default=_mapping_bool(older, "deploy", default=False),
    )

    return EffectScope(isolated_branch=isolated, merge=merge, deploy=deploy)


def execution_decision(
    *,
    action: str,
    explicitly_assigned: bool,
    reversible: bool,
    protected: bool,
    ambiguous: bool,
) -> str:
    if not isinstance(action, str) or not action.strip():
        raise WorkflowError("ACTION_REQUIRED")
    explicitly_assigned = _require_bool(
        explicitly_assigned, "explicitly_assigned"
    )
    reversible = _require_bool(reversible, "reversible")
    protected = _require_bool(protected, "protected")
    ambiguous = _require_bool(ambiguous, "ambiguous")

    if ambiguous:
        return DECISION_REQUIRE_CLARIFICATION
    if protected:
        return DECISION_REQUIRE_EXACT_AUTHORITY
    if explicitly_assigned and reversible:
        return DECISION_DO_VERIFY_REPORT
    if not explicitly_assigned:
        return DECISION_REQUIRE_ASSIGNMENT
    return DECISION_REQUIRE_EXACT_AUTHORITY


class DependencyGraph:
    """Simple dependency graph for assignment readiness and protected authority."""

    def __init__(self) -> None:
        self._assignments: Set[str] = set()
        self._complete: Set[str] = set()
        self._predecessors: Dict[str, Set[str]] = {}
        self._protected_authority: Set[str] = set()

    def add_assignment(
        self,
        assignment_id: str,
        *,
        protected_authority: bool = False,
    ) -> None:
        assignment = _assignment_id(assignment_id)
        protected = _require_bool(protected_authority, "protected_authority")
        if assignment in self._assignments:
            raise WorkflowError(f"ASSIGNMENT_COLLISION: {assignment}")
        self._assignments.add(assignment)
        self._predecessors[assignment] = set()
        if protected:
            self._protected_authority.add(assignment)

    def add_edge(self, predecessor: str, successor: str) -> None:
        predecessor = self._require(predecessor)
        successor = self._require(successor)
        if predecessor == successor or self._reachable(successor, predecessor):
            raise WorkflowError(f"DEPENDENCY_CYCLE: {predecessor} -> {successor}")
        self._predecessors[successor].add(predecessor)

    def complete(self, assignment_id: str) -> None:
        assignment = self._require(assignment_id)
        self._complete.add(assignment)

    def ready(self, assignment_id: str) -> bool:
        assignment = self._require(assignment_id)
        return self._predecessors.get(assignment, set()).issubset(self._complete)

    def protected_authority(self, assignment_id: str) -> bool:
        assignment = self._require(assignment_id)
        return assignment in self._protected_authority

    def _require(self, assignment_id: str) -> str:
        assignment = _assignment_id(assignment_id)
        if assignment not in self._assignments:
            raise WorkflowError(f"UNKNOWN_ASSIGNMENT: {assignment}")
        return assignment

    def _reachable(self, start: str, target: str) -> bool:
        stack = [start]
        seen: Set[str] = set()
        reverse: Dict[str, Set[str]] = {a: set() for a in self._assignments}
        for successor, predecessors in self._predecessors.items():
            for predecessor in predecessors:
                reverse.setdefault(predecessor, set()).add(successor)

        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(reverse.get(current, ()))
        return False


__all__ = [
    "DependencyGraph",
    "EffectScope",
    "WorkflowError",
    "classify_lane_requirement",
    "execution_decision",
    "resolve_effect_scope",
]
