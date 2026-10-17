"""Source interface shared by DART, GLEIF, SEC EDGAR and Companies House."""

from __future__ import annotations

from dataclasses import dataclass, field

from tracer.http import Http
from tracer.models import Entity, OwnersResult, Unknown


@dataclass
class Candidate:
    entity: Entity
    score: float  # name similarity 0-100 (100 for an identifier lookup)
    matched_name: str = ""


@dataclass
class LookupResult:
    candidates: list[Candidate] = field(default_factory=list)
    unknowns: list[Unknown] = field(default_factory=list)


class Source:
    """One public data source. Methods never raise: failures become ``Unknown`` entries."""

    name: str = ""
    label_ko: str = ""

    def __init__(self, http: Http):
        self.http = http

    def available(self) -> tuple[bool, str]:
        """(usable, digit-free reason when not usable)."""
        return True, ""

    def handles(self, entity: Entity) -> bool:
        """True if this source can say who owns ``entity``."""
        return False

    def search(self, name: str) -> LookupResult:
        """Find entities by name."""
        return LookupResult()

    def owners(self, entity: Entity) -> OwnersResult:
        return OwnersResult(source=self.name)

    def unavailable_unknown(self) -> Unknown:
        _, reason = self.available()
        return Unknown(f"{self.name}_unavailable", f"{self.label_ko}: {reason}")


def failure(source: str, label_ko: str, exc: Exception) -> Unknown:
    return Unknown(f"{source}_error", f"{label_ko} 호출 실패, 이 구간은 확인하지 못함", f"{type(exc).__name__}: {exc}"[:200])
