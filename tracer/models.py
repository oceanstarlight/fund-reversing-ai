"""Core data model: entities, ownership edges with evidence, and the traced graph.

Every edge carries the documents it rests on (``Evidence``) and a three-level confidence.
Share figures are stored exactly as the source states them (``share_text``); ``share_pct``
is filled only when the source gives a single number, never by inference.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Confidence(str, Enum):
    CONFIRMED = "confirmed"  # 확인됨: the source states the link directly
    ESTIMATED = "estimated"  # 추정: link rests on a name match, self-report or extracted text
    UNVERIFIABLE = "unverifiable"  # 확인 불가: public sources do not settle it


CONFIDENCE_KO = {Confidence.CONFIRMED: "확인됨", Confidence.ESTIMATED: "추정", Confidence.UNVERIFIABLE: "확인 불가"}


class EntityKind(str, Enum):
    COMPANY = "company"
    INDIVIDUAL = "individual"
    FOUNDATION = "foundation"
    PUBLIC_BODY = "public_body"  # state, pension fund, public corporation
    FUND = "fund"  # investment fund or asset manager holding for clients
    UNKNOWN = "unknown"


class Relation(str, Enum):
    SHAREHOLDING = "shareholding"  # DART 최대주주·특수관계인 지분
    PSC = "psc"  # Companies House person with significant control
    BENEFICIAL_OWNER = "beneficial_owner"  # SEC Schedule 13D/13G
    ACCOUNTING_PARENT = "accounting_parent"  # GLEIF direct accounting consolidation parent
    ULTIMATE_ACCOUNTING_PARENT = "ultimate_accounting_parent"  # GLEIF ultimate parent (label only)
    DOCUMENT = "document"  # extracted from LPA/PPM text, always reviewed


RELATION_KO = {
    Relation.SHAREHOLDING: "지분 보유(공시)",
    Relation.PSC: "실질 지배(PSC)",
    Relation.BENEFICIAL_OWNER: "5% 이상 보유(13D/G)",
    Relation.ACCOUNTING_PARENT: "직접 연결 모회사(회계상)",
    Relation.ULTIMATE_ACCOUNTING_PARENT: "최종 연결 모회사(회계상)",
    Relation.DOCUMENT: "문서 추출",
}
# Relations that express ownership or control; the accounting ones are shown separately.
OWNERSHIP_RELATIONS = {Relation.SHAREHOLDING, Relation.PSC, Relation.BENEFICIAL_OWNER, Relation.DOCUMENT}


class Terminal(str, Enum):
    """Why tracing stopped at an entity (README §6 step 4)."""

    INDIVIDUAL = "individual"  # 공시상 확인된 개인
    FOUNDATION = "foundation"  # 공익재단
    PUBLIC_BODY = "public_body"  # 국가·공공기관
    DISPERSED = "dispersed"  # 지배주주 없는 분산 소유
    FUND = "fund"  # 운용사·펀드: 고객 자산을 보유하므로 그 위로 추적하지 않음
    ULTIMATE_PARENT = "ultimate_parent"  # 최종 연결 모회사(회계상), 그 위 소유는 미확인
    DATA_END = "data_end"  # 공개 데이터의 끝
    UNSUPPORTED = "unsupported"  # 미지원 관할
    UNRESOLVED = "unresolved"  # 이름을 법인으로 특정하지 못함
    LIMIT = "limit"  # 깊이·개수 제한


TERMINAL_KO = {
    Terminal.INDIVIDUAL: "공시상 확인된 개인",
    Terminal.FOUNDATION: "공시상 확인된 재단",
    Terminal.PUBLIC_BODY: "국가·공공기관",
    Terminal.DISPERSED: "지배주주 없는 분산 소유",
    Terminal.FUND: "운용사·펀드(고객 자산 보유)",
    Terminal.ULTIMATE_PARENT: "최종 연결 모회사(회계상)",
    Terminal.DATA_END: "공개 정보로 확인 불가",
    Terminal.UNSUPPORTED: "미지원 관할",
    Terminal.UNRESOLVED: "법인 특정 실패",
    Terminal.LIMIT: "추적 제한 도달",
}


@dataclass
class Evidence:
    source: str  # dart | gleif | sec | companies_house | document
    title: str
    url: str  # human-openable link, no credentials
    as_of: str = ""  # 기준 시점 of the stated fact (report date, notified_on, ...)
    retrieved_at: str = field(default_factory=now_utc)
    locator: str = ""  # page, section or field inside the document
    quote: str = ""  # verbatim source text the value was read from


@dataclass
class Unknown:
    code: str
    message: str  # digit-free, safe to pass to an LLM
    detail: str = ""  # may contain numbers, UI only


@dataclass
class Entity:
    id: str  # "<scheme>:<value>", e.g. lei:..., dart:00126380, ch:00000006, cik:0000320193
    name: str
    kind: EntityKind = EntityKind.COMPANY
    jurisdiction: str = ""  # ISO country, optionally with subdivision ("US-DE")
    identifiers: dict[str, str] = field(default_factory=dict)  # scheme -> value
    terminal: Terminal | None = None
    terminal_note: str = ""
    unsupported_jurisdiction: bool = False

    @property
    def country(self) -> str:
        return self.jurisdiction.split("-")[0].upper() if self.jurisdiction else ""


@dataclass
class Edge:
    owner_id: str
    owned_id: str
    relation: Relation
    confidence: Confidence
    share_text: str = ""  # as stated by the source ("21.06", "75~100%")
    share_pct: float | None = None  # only when the source gives one number
    evidence: list[Evidence] = field(default_factory=list)
    note: str = ""


@dataclass
class ReviewItem:
    code: str
    message: str
    target: str  # entity id or "owner_id->owned_id"
    priority: int = 2  # 1 high, 2 normal, 3 low
    links: list[str] = field(default_factory=list)


@dataclass
class Flag:
    kind: str  # "circular" (순환출자) | "mutual" (상호출자)
    path: list[str]


@dataclass
class OwnersResult:
    """What one source says about the owners of one entity."""

    source: str
    edges: list[Edge] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)  # owners referenced by ``edges``
    terminal: Terminal | None = None  # set when the source says tracing should stop here
    terminal_note: str = ""
    unknowns: list[Unknown] = field(default_factory=list)
    review: list[ReviewItem] = field(default_factory=list)


@dataclass
class TraceStep:
    entity_id: str
    source: str
    outcome: str  # short Korean description for the log panel


@dataclass
class OwnershipGraph:
    query: str
    root_id: str = ""
    entities: dict[str, Entity] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    review: list[ReviewItem] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    unknowns: list[Unknown] = field(default_factory=list)
    steps: list[TraceStep] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)  # set when the input needs a person's choice
    created_at: str = field(default_factory=now_utc)
    _alias: dict[str, str] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ entities
    def canonical(self, entity_id: str) -> str:
        return self._alias.get(entity_id, entity_id)

    def find(self, entity: Entity) -> Entity | None:
        """Existing entity sharing the id or any identifier with ``entity``."""
        for key in [entity.id, *(f"{k}:{v}" for k, v in entity.identifiers.items())]:
            cid = self._alias.get(key)
            if cid and cid in self.entities:
                return self.entities[cid]
        return None

    def add_entity(self, entity: Entity) -> Entity:
        """Insert or merge; returns the canonical entity."""
        existing = self.find(entity)
        if existing is None:
            self.entities[entity.id] = entity
            existing = entity
        else:
            for k, v in entity.identifiers.items():
                existing.identifiers.setdefault(k, v)
            if existing.kind == EntityKind.UNKNOWN:
                existing.kind = entity.kind
            existing.jurisdiction = existing.jurisdiction or entity.jurisdiction
        for key in [entity.id, existing.id, *(f"{k}:{v}" for k, v in existing.identifiers.items())]:
            self._alias[key] = existing.id
        return existing

    def add_edge(self, edge: Edge) -> Edge:
        edge.owner_id, edge.owned_id = self.canonical(edge.owner_id), self.canonical(edge.owned_id)
        for e in self.edges:
            if (e.owner_id, e.owned_id, e.relation) == (edge.owner_id, edge.owned_id, edge.relation):
                e.evidence.extend(edge.evidence)
                return e
        self.edges.append(edge)
        return edge

    def owners_of(self, entity_id: str) -> list[Edge]:
        return [e for e in self.edges if e.owned_id == entity_id]

    def terminals(self) -> list[Entity]:
        return [e for e in self.entities.values() if e.terminal is not None]

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_alias", None)
        return d
