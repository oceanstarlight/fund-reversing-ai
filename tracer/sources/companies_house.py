"""UK Companies House: company search and the PSC register, read from the local bulk index.

PSC ("person with significant control") entries name individuals or legal entities with
more than a quarter of shares or votes, or other control. Ownership is published as a band
(e.g. 25-50%), so ``share_text`` keeps the band and ``share_pct`` stays empty.

The data comes from Companies House's free bulk downloads (``ch_index.py``), so no account or
API key is needed; the trade-off is a daily (PSC) and monthly (names) refresh instead of live.
"""

from __future__ import annotations

import re

from tracer import names
from tracer.models import (
    Confidence, Edge, Entity, EntityKind, Evidence, OwnersResult, Relation, ReviewItem, Terminal, Unknown,
)
from tracer.sources.base import Candidate, LookupResult, Source, failure
from tracer.sources.ch_index import ChIndex

WEB = "https://find-and-update.company-information.service.gov.uk"
_UK = re.compile(r"england|wales|scotland|northern ireland|united kingdom|\buk\b|great britain|companies house", re.I)
_BAND = re.compile(r"(ownership-of-shares|voting-rights)-(\d+)-to-(\d+)-percent")
_DISPERSED_STATEMENTS = {"no-individual-or-entity-with-signficant-control",
                         "no-individual-or-entity-with-significant-control"}


def psc_url(number: str) -> str:
    return f"{WEB}/company/{number}/persons-with-significant-control"


def describe_control(natures: list[str]) -> str:
    """Korean summary of PSC natures_of_control, keeping the published bands."""
    parts = []
    for n in natures:
        m = _BAND.search(n)
        if m:
            what = "주식" if m.group(1) == "ownership-of-shares" else "의결권"
            parts.append(f"{what} {m.group(2)}~{m.group(3)}%")
        elif "appoint-and-remove-directors" in n:
            parts.append("이사 선임·해임권")
        elif "significant-influence-or-control" in n:
            parts.append("중대한 영향력·지배")
    return " / ".join(dict.fromkeys(parts))


def normalize_number(number: str) -> str:
    n = number.strip().upper().replace(" ", "")
    return n.zfill(8) if n.isdigit() else n


class CompaniesHouseSource(Source):
    name = "companies_house"
    label_ko = "Companies House"

    def __init__(self, http, index: ChIndex | None = None):
        super().__init__(http)
        self.index = index or ChIndex()

    def available(self) -> tuple[bool, str]:
        return (True, "") if self.index.exists() else (False, "영국 일괄 데이터 색인이 없어 PSC를 조회하지 못함")

    def handles(self, entity: Entity) -> bool:
        return "ch" in entity.identifiers

    def get(self, number: str) -> Entity | None:
        number = normalize_number(number)
        row = self.index.company(number) if self.index.exists() else None
        if row is None:
            return None
        return Entity(id=f"ch:{number}", name=row[0], kind=EntityKind.COMPANY, jurisdiction="GB", identifiers={"ch": number})

    def search(self, name: str) -> LookupResult:
        out = LookupResult()
        ok, _ = self.available()
        if not ok:
            out.unknowns.append(self.unavailable_unknown())
            return out
        try:
            for number, title, _status in self.index.search(name):
                e = Entity(id=f"ch:{number}", name=title, jurisdiction="GB", identifiers={"ch": number})
                out.candidates.append(Candidate(e, names.score(name, title), title))
        except Exception as exc:  # noqa: BLE001
            out.unknowns.append(failure(self.name, self.label_ko, exc))
        out.candidates.sort(key=lambda c: -c.score)
        del out.candidates[10:]
        return out

    def owners(self, entity: Entity) -> OwnersResult:
        res = OwnersResult(source=self.name)
        ok, _ = self.available()
        if not ok:
            res.unknowns.append(self.unavailable_unknown())
            return res
        number = entity.identifiers["ch"]
        try:
            active = self.index.psc(number)  # ceased entries are dropped when the index is built
            for it in active:
                self._add_psc(res, entity, number, it)
            if not active:
                self._statements(res, entity, number)
        except Exception as exc:  # noqa: BLE001
            res.unknowns.append(failure(self.name, self.label_ko, exc))
        return res

    def _add_psc(self, res: OwnersResult, entity: Entity, number: str, it: dict) -> None:
        kind = it.get("kind", "")
        nm = it.get("name") or "(이름 비공개)"
        control = describe_control(it.get("natures_of_control") or [])
        ev = Evidence(source=self.name, title=f"PSC register · {entity.name}", url=psc_url(number),
                      as_of=it.get("notified_on", ""), locator=f"{kind} · snapshot {self.index.meta('psc_snapshot')}",
                      quote=f"{nm} | {', '.join(it.get('natures_of_control') or [])}")
        conf = Confidence.CONFIRMED
        if kind.startswith("individual"):
            owner = Entity(id=f"person:{names.slug(nm)}", name=nm, kind=EntityKind.INDIVIDUAL,
                           jurisdiction=(it.get("country_of_residence") or ""))
        elif kind.startswith("super-secure"):
            owner = Entity(id=f"person:secure:{number}", name="(보호 대상 개인, 비공개)", kind=EntityKind.INDIVIDUAL)
            conf = Confidence.UNVERIFIABLE
            res.review.append(ReviewItem("ch_super_secure", f"{entity.name}: PSC가 보호 대상 개인으로 비공개",
                                         f"{owner.id}->{entity.id}", 1, [ev.url]))
        else:
            ident = it.get("identification") or {}
            reg = (ident.get("registration_number") or "").strip()
            where = " ".join(filter(None, [ident.get("country_registered"), ident.get("place_registered")]))
            legal_person = kind.startswith("legal-person")
            k = EntityKind.PUBLIC_BODY if legal_person else names.classify_holder(nm)
            k = EntityKind.COMPANY if k in (EntityKind.UNKNOWN, EntityKind.INDIVIDUAL) else k
            if reg and _UK.search(where) and not legal_person:
                n = normalize_number(reg)
                owner = Entity(id=f"ch:{n}", name=nm, kind=k, jurisdiction="GB", identifiers={"ch": n})
            else:
                owner = Entity(id=f"name:{names.slug(nm)}", name=nm, kind=k,
                               identifiers={"registered_in": where} if where else {})
        res.entities.append(owner)
        res.edges.append(Edge(owner.id, entity.id, Relation.PSC, conf, share_text=control, evidence=[ev],
                              note="PSC 지분은 구간으로만 공시됨"))

    def _statements(self, res: OwnersResult, entity: Entity, number: str) -> None:
        codes = {s["statement"] for s in self.index.statements(number) if s["statement"]}
        if codes & _DISPERSED_STATEMENTS:
            res.terminal, res.terminal_note = Terminal.DISPERSED, "Companies House: 중대한 지배자 없음 진술"
            return
        res.terminal = Terminal.DATA_END
        if codes:
            res.terminal_note = "Companies House: PSC 미확정 진술 (" + ", ".join(sorted(codes)) + ")"
            res.unknowns.append(Unknown("ch_psc_statement", "영국 PSC가 확정되지 않았다는 진술만 있음", ", ".join(sorted(codes))))
        else:
            res.terminal_note = "Companies House: PSC 정보 없음 (상장사는 PSC 공시가 면제될 수 있음)"
            res.unknowns.append(Unknown("ch_no_psc", "영국 PSC 정보가 없음, 상장사 면제일 수 있음"))
        res.review.append(ReviewItem("ch_no_psc", f"{entity.name}: {res.terminal_note}", entity.id, 2, [psc_url(number)]))
