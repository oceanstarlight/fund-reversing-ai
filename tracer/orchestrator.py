"""Orchestrator: normalize the input, route each entity to sources, walk owners upward.

Deterministic end to end (README §6):
1. Resolve the input (LEI, business registration number, stock/corp code, name) to one entity.
   A name that does not match exactly and uniquely is returned as candidates for a person.
2. For each entity, link identifiers across sources (DART <-> GLEIF by registration number,
   GLEIF <-> Companies House by company number, name matches otherwise).
3. Ask the ownership source of its jurisdiction (KR: DART, GB: Companies House, US: SEC) and
   GLEIF for the accounting parent. GLEIF parents are followed only when no ownership source
   answered, so the chain prefers disclosed ownership over accounting consolidation.
4. Stop at individuals, foundations, public bodies, funds, dispersed ownership or the end of
   public data; flag circular and mutual shareholdings without walking them twice.
"""

from __future__ import annotations

import re
from datetime import date

from tracer import config, names
from tracer.http import Http
from tracer.models import (
    OWNERSHIP_RELATIONS, TERMINAL_KO, Confidence, Edge, Entity, EntityKind, Flag, OwnersResult,
    OwnershipGraph, Relation, ReviewItem, Terminal, TraceStep, Unknown,
)
from tracer.sources.base import Candidate, LookupResult
from tracer.sources.companies_house import CompaniesHouseSource
from tracer.sources.dart import DartSource
from tracer.sources.gleif import LEI_RE, GleifSource
from tracer.sources.sec import SecSource

SUPPORTED = {"KR": "dart", "GB": "companies_house", "US": "sec"}
KIND_TERMINAL = {
    EntityKind.INDIVIDUAL: Terminal.INDIVIDUAL,
    EntityKind.FOUNDATION: Terminal.FOUNDATION,
    EntityKind.PUBLIC_BODY: Terminal.PUBLIC_BODY,
    EntityKind.FUND: Terminal.FUND,
}
_BRN = re.compile(r"^\d{3}-?\d{2}-?\d{5}$")
_PREFIXED = re.compile(r"^(lei|dart|ch|cik|krx):\s*(\S+)$", re.IGNORECASE)


class Tracer:
    def __init__(self, http: Http | None = None, today: date | None = None):
        self.http = http or Http()
        self.dart = DartSource(self.http, today)
        self.gleif = GleifSource(self.http)
        self.sec = SecSource(self.http, today)
        self.ch = CompaniesHouseSource(self.http)
        self.ownership_sources = {"dart": self.dart, "companies_house": self.ch, "sec": self.sec}

    # ================================================================ resolution
    def resolve(self, query: str) -> tuple[Entity | None, list[Candidate], list[Unknown]]:
        """(entity, candidates for a person to choose from, unknowns). Exactly one of the first
        two is set when resolution worked."""
        q = query.strip()
        unknowns: list[Unknown] = []
        try:
            m = _PREFIXED.match(q)
            if m:
                scheme, value = m.group(1).lower(), m.group(2)
                ent = {
                    "lei": lambda v: self.gleif.get(v.upper()),
                    "dart": self.dart.by_code, "krx": self.dart.by_code,
                    "ch": self.ch.get, "cik": self.sec.get,
                }[scheme](value)
                return ent, [], unknowns if ent else [Unknown("not_found", "입력한 식별자로 법인을 찾지 못함", q)]
            if LEI_RE.match(q.upper()):
                ent = self.gleif.get(q.upper())
                return ent, [], [] if ent else [Unknown("not_found", "입력한 LEI를 찾지 못함", q)]
            if _BRN.match(q):
                found = self.gleif.by_registration(q.replace("-", ""))
                unknowns += found.unknowns
                if found.candidates:
                    return found.candidates[0].entity, [], unknowns
                return None, [], unknowns + [Unknown("brn_not_found", "사업자등록번호로 등록된 LEI를 찾지 못함, 기업명으로 다시 입력 필요", q)]
            if re.fullmatch(r"\d{6}|\d{8}", q) and self.dart.available()[0]:
                ent = self.dart.by_code(q)
                if ent:
                    return ent, [], unknowns
        except Exception as exc:  # noqa: BLE001
            unknowns.append(Unknown("resolve_error", "입력 식별자 조회 중 오류", f"{type(exc).__name__}: {exc}"[:200]))
            return None, [], unknowns
        return self._resolve_name(q, unknowns)

    def _resolve_name(self, q: str, unknowns: list[Unknown]) -> tuple[Entity | None, list[Candidate], list[Unknown]]:
        order = [self.dart, self.gleif] if names.is_korean(q) else [self.sec, self.ch, self.gleif, self.dart]
        cands: list[tuple[str, Candidate]] = []
        for src in order:
            if not src.available()[0]:
                continue
            res: LookupResult = src.search(q)
            unknowns += res.unknowns
            cands += [(src.name, c) for c in res.candidates]
        if not cands:
            return None, [], unknowns + [Unknown("no_candidates", "이름으로 찾은 후보가 없음", q)]
        exact = [(s, c) for s, c in cands if c.score >= config.NAME_MATCH_AUTO]
        per_source: dict[str, set[str]] = {}
        for s, c in exact:
            per_source.setdefault(s, set()).add(c.entity.id)
        if exact and all(len(v) == 1 for v in per_source.values()):
            # One exact hit per source: the same entity seen by several registries. Start from
            # the first in priority order; the others are joined by identifier linking.
            return exact[0][1].entity, [], unknowns
        top = sorted((c for _, c in cands), key=lambda c: -c.score)[:8]
        unknowns.append(Unknown("name_ambiguous", "입력한 이름이 하나의 법인으로 확정되지 않아 사람 확인이 필요함"))
        return None, top, unknowns

    # =================================================================== linking
    def link(self, ent: Entity) -> list[Unknown]:
        """Add identifiers from other sources. Only identifier joins and exact name matches
        are applied silently; anything weaker is left for review."""
        unknowns: list[Unknown] = []
        ids = ent.identifiers
        try:
            if "dart" in ids and "lei" not in ids and self.dart.available()[0]:
                self.dart.enrich(ent)
                if "brn" in ids:
                    hit = self.gleif.by_registration(ids["brn"])
                    if hit.candidates:
                        ids["lei"] = hit.candidates[0].entity.identifiers["lei"]
            if "lei" in ids and ent.country == "KR" and "dart" not in ids and self.dart.available()[0]:
                hit = self._exact(self.dart.search(ent.name))
                if hit:
                    ids.update({k: v for k, v in hit.identifiers.items() if k != "alt_name"})
            if "lei" in ids and ent.country == "US" and "cik" not in ids and self.sec.available()[0]:
                hit = self._exact(self.sec.search(ent.name))
                if hit:
                    ids["cik"] = hit.identifiers["cik"]
            if "lei" not in ids and ("ch" in ids or "cik" in ids):
                hit = self._exact(self.gleif.search(ent.name), country=ent.country)
                if hit:
                    ids["lei"] = hit.identifiers["lei"]
                    ids.update({k: v for k, v in hit.identifiers.items() if k == "ch" and "ch" not in ids})
            if "lei" in ids and not ent.jurisdiction:
                g = self.gleif.get(ids["lei"])
                ent.jurisdiction = g.jurisdiction if g else ""
        except Exception as exc:  # noqa: BLE001
            unknowns.append(Unknown("link_error", "다른 소스와 식별자를 연결하지 못함", f"{type(exc).__name__}: {exc}"[:200]))
        return unknowns

    @staticmethod
    def _exact(res: LookupResult, country: str = "") -> Entity | None:
        hits = [c.entity for c in res.candidates if c.score >= config.NAME_MATCH_AUTO
                and (not country or not c.entity.country or c.entity.country == country)]
        return hits[0] if len({h.id for h in hits}) == 1 else None

    def resolve_holder(self, owner: Entity) -> tuple[Entity, Confidence | None, list[Candidate]]:
        """Turn a name-only holder into a registered entity.

        Returns (entity, confidence of the name link or None when it was not linked, candidates).
        """
        if not owner.id.startswith("name:") or owner.kind not in (EntityKind.COMPANY, EntityKind.UNKNOWN):
            return owner, Confidence.CONFIRMED, []
        order = [self.dart, self.gleif] if names.is_korean(owner.name) else [self.sec, self.ch, self.gleif]
        cands: list[Candidate] = []
        hint = owner.identifiers.get("registered_in", "")
        for src in order:
            if not src.available()[0]:
                continue
            cands += src.search(owner.name).candidates
        if hint:
            for c in cands:
                if c.entity.country and _country_hint(hint) and c.entity.country != _country_hint(hint):
                    c.score -= 10  # registered elsewhere than the filing says
        cands.sort(key=lambda c: -c.score)
        if not cands:
            return owner, None, []
        best = cands[0]
        conf = names.match_confidence(best.score)
        rivals = [c for c in cands[1:] if c.score >= config.NAME_MATCH_AUTO and c.entity.id != best.entity.id
                  and not set(c.entity.identifiers.items()) & set(best.entity.identifiers.items())]
        if conf == Confidence.CONFIRMED and rivals:
            conf = Confidence.ESTIMATED
        if conf is None:
            return owner, None, cands[:5]
        ent = best.entity
        ent.kind = owner.kind if owner.kind != EntityKind.UNKNOWN else ent.kind
        return ent, conf, cands[:5]

    # ==================================================================== trace
    def trace(self, query: str) -> OwnershipGraph:
        g = OwnershipGraph(query=query)
        root, cands, unknowns = self.resolve(query)
        g.unknowns += unknowns
        if root is None:
            g.candidates = [{"id": c.entity.id, "name": c.entity.name, "jurisdiction": c.entity.jurisdiction,
                             "score": round(c.score, 1)} for c in cands]
            g.review.append(ReviewItem("input_unresolved", "입력을 하나의 법인으로 확정하지 못함: 후보 중 선택하거나 식별자(LEI 등)로 다시 입력",
                                       query, 1))
            return g
        root = g.add_entity(root)
        g.root_id = root.id
        queue: list[tuple[str, int]] = [(root.id, 0)]
        expanded: set[str] = set()
        while queue:
            eid, depth = queue.pop(0)
            eid = g.canonical(eid)
            if eid in expanded:
                continue
            expanded.add(eid)
            ent = g.entities[eid]
            if ent.kind in KIND_TERMINAL:
                ent.terminal = ent.terminal or KIND_TERMINAL[ent.kind]
                continue
            if depth >= config.MAX_DEPTH or len(expanded) > config.MAX_ENTITIES:
                ent.terminal, ent.terminal_note = Terminal.LIMIT, "깊이 또는 법인 수 제한"
                continue
            g.unknowns += self.link(ent)
            g.add_entity(ent)  # register identifiers added by linking
            for owner_id in self._expand(g, ent):
                queue.append((owner_id, depth + 1))
        g.flags = find_cycles(g)
        finalize_review(g)
        return g

    def _expand(self, g: OwnershipGraph, ent: Entity) -> list[str]:
        """Query sources for ``ent``, add results to the graph, return owner ids to walk next."""
        results: list[OwnersResult] = []
        country = ent.country
        own_name = SUPPORTED.get(country)
        if not country or own_name is None:
            ent.unsupported_jurisdiction = bool(country)
            if country:
                g.unknowns.append(Unknown("unsupported_jurisdiction", "미지원 관할이라 회계상 모회사만 조회함", f"{ent.name}: {country}"))
        own = self.ownership_sources.get(own_name) if own_name else None
        if own is not None:
            if own.handles(ent):
                results.append(own.owners(ent))
            elif not own.available()[0]:
                g.unknowns.append(own.unavailable_unknown())
            else:
                g.unknowns.append(Unknown("no_registry_id", f"{own.label_ko}에서 이 법인을 찾지 못해 소유 공시를 조회하지 못함", ent.name))
        if self.gleif.handles(ent):
            results.append(self.gleif.owners(ent))

        has_ownership = False
        next_ids: list[str] = []
        accounting: list[Edge] = []
        terminals: list[tuple[str, Terminal | None, str]] = []
        for res in results:
            g.unknowns += res.unknowns
            g.review += res.review
            mapping = {}
            for owner in res.entities:
                linked, conf, cands = self.resolve_holder(owner)
                if conf is None:
                    linked.terminal, linked.terminal_note = Terminal.UNRESOLVED, "등록 법인으로 특정하지 못함"
                    g.review.append(ReviewItem("holder_unresolved", f"보유자 '{owner.name}'을(를) 등록 법인으로 특정하지 못함",
                                               owner.id, 1, []))
                linked = g.add_entity(linked)
                if linked.kind in KIND_TERMINAL and linked.terminal is None:
                    linked.terminal = KIND_TERMINAL[linked.kind]
                mapping[owner.id] = (linked, conf)
            for edge in res.edges:
                owner, conf = mapping.get(edge.owner_id, (g.entities.get(edge.owner_id), Confidence.CONFIRMED))
                edge.owner_id = owner.id
                if conf == Confidence.ESTIMATED and edge.confidence == Confidence.CONFIRMED:
                    edge.confidence = Confidence.ESTIMATED
                    g.review.append(ReviewItem("holder_name_match", f"보유자 '{owner.name}' 연결은 이름 유사도 기반",
                                               f"{owner.id}->{ent.id}", 2, [ev.url for ev in edge.evidence]))
                edge = g.add_edge(edge)
                if edge.relation in OWNERSHIP_RELATIONS:
                    has_ownership = True
                    if _follow(edge, owner):
                        next_ids.append(owner.id)
                elif edge.relation == Relation.ACCOUNTING_PARENT:
                    accounting.append(edge)
            terminals.append((res.source, res.terminal, res.terminal_note))
            g.steps.append(TraceStep(ent.id, res.source, _step_text(res)))

        if not has_ownership:
            next_ids += [e.owner_id for e in accounting]
        # An ownership source's stop reason wins; GLEIF's counts only when no ownership was found.
        stops = [(t, n) for src, t, n in terminals if t is not None and (src != "gleif" or not has_ownership)]
        stops.sort(key=lambda x: x[0] != Terminal.DISPERSED)
        terminal, note = stops[0] if stops else (None, "")
        if terminal is not None and not next_ids:
            ent.terminal, ent.terminal_note = terminal, note
        elif terminal == Terminal.DISPERSED:
            ent.terminal, ent.terminal_note = terminal, note  # holders kept for display
        elif not next_ids and not g.owners_of(ent.id):
            ent.terminal = Terminal.UNSUPPORTED if ent.unsupported_jurisdiction and not self.gleif.handles(ent) else Terminal.DATA_END
            ent.terminal_note = "공개 소스에서 상위 소유자를 찾지 못함"
        if ent.terminal == Terminal.DATA_END and not has_ownership and any(
                e.relation == Relation.ACCOUNTING_PARENT for e in g.edges if e.owner_id == ent.id):
            ent.terminal, ent.terminal_note = Terminal.ULTIMATE_PARENT, f"{note} · 그 위 실질 소유는 미확인".strip(" ·")
        return list(dict.fromkeys(next_ids))


def _follow(edge: Edge, owner: Entity) -> bool:
    if owner.kind not in (EntityKind.COMPANY, EntityKind.UNKNOWN) or owner.terminal == Terminal.UNRESOLVED:
        return False
    if edge.confidence == Confidence.UNVERIFIABLE:
        return False
    return edge.share_pct is None or edge.share_pct >= config.FOLLOW_MIN_PCT


def _country_hint(text: str) -> str:
    t = text.lower()
    for pat, cc in ((r"united kingdom|england|wales|scotland|\buk\b", "GB"), (r"korea|대한민국|한국", "KR"),
                    (r"united states|delaware|new york|\busa?\b", "US"), (r"cayman", "KY"), (r"virgin islands|bvi", "VG"),
                    (r"japan", "JP"), (r"singapore", "SG"), (r"netherlands", "NL"), (r"luxembourg", "LU")):
        if re.search(pat, t):
            return cc
    return text.upper() if re.fullmatch(r"[A-Za-z]{2}", text) and text.upper() not in {"DE", "NY", "CA", "PA", "MA"} else ""


def _step_text(res: OwnersResult) -> str:
    if res.edges:
        return f"상위 관계 {len(res.edges)}건"
    if res.terminal is not None:
        return f"종착: {TERMINAL_KO[res.terminal]}"
    return "결과 없음" + (f" ({res.unknowns[0].message})" if res.unknowns else "")


def find_cycles(g: OwnershipGraph) -> list[Flag]:
    """Circular (순환출자) and mutual (상호출자) shareholdings as strongly connected components."""
    adj: dict[str, list[str]] = {}
    for e in g.edges:
        if e.relation in OWNERSHIP_RELATIONS:
            adj.setdefault(e.owner_id, []).append(e.owned_id)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    out: list[Flag] = []
    counter = [0]

    def strong(v: str) -> None:  # Tarjan; graphs here are small, recursion depth is bounded
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in adj.get(v, []):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(Flag("mutual" if len(comp) == 2 else "circular", sorted(comp)))

    for v in list(adj):
        if v not in index:
            strong(v)
    return out


def finalize_review(g: OwnershipGraph) -> None:
    """Add review items for stops and weak links not already listed, then sort and dedupe."""
    for ent in g.entities.values():
        if ent.terminal in (Terminal.DATA_END, Terminal.UNSUPPORTED, Terminal.LIMIT, Terminal.ULTIMATE_PARENT):
            g.review.append(ReviewItem(f"stop_{ent.terminal.value}", f"{ent.name}: {TERMINAL_KO[ent.terminal]}"
                                       + (f" ({ent.terminal_note})" if ent.terminal_note else ""), ent.id, 2))
        if ent.kind == EntityKind.INDIVIDUAL and ent.name.startswith("("):
            g.review.append(ReviewItem("individual_undisclosed", f"종착 개인의 실명이 비공개: 추가 확인 필요", ent.id, 1))
    targeted = {r.target for r in g.review}
    for e in g.edges:
        if e.confidence != Confidence.CONFIRMED and f"{e.owner_id}->{e.owned_id}" not in targeted:
            g.review.append(ReviewItem(f"edge_{e.confidence.value}", f"{g.entities[e.owner_id].name} → {g.entities[e.owned_id].name}: "
                                       f"{'추정' if e.confidence == Confidence.ESTIMATED else '확인 불가'} 연결", f"{e.owner_id}->{e.owned_id}", 2,
                                       [ev.url for ev in e.evidence]))
    for f in g.flags:
        g.review.append(ReviewItem(f"flag_{f.kind}", ("상호출자" if f.kind == "mutual" else "순환출자") + " 구조 감지: "
                                   + " · ".join(g.entities[i].name for i in f.path), ",".join(f.path), 1))
    seen, out = set(), []
    for r in sorted(g.review, key=lambda r: r.priority):
        key = (r.target, r.code)
        if key not in seen:
            seen.add(key)
            out.append(r)
    g.review = out
