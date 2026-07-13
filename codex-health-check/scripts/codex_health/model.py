from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass(frozen=True)
class Finding:
    rule_id: str
    domain: str
    priority: str
    confidence: str
    title: str
    evidence: str
    impact: str
    recommendation: str
    requires_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModuleResult:
    name: str
    status: str = "complete"
    summary: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "findings": [item.to_dict() for item in self.findings],
            "notes": self.notes,
        }


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda item: (PRIORITY_ORDER[item.priority], item.domain, item.rule_id),
    )
