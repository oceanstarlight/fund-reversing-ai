"""GLEIF API (no key): LEI records, direct/ultimate accounting parents and reporting exceptions.

GLEIF parents are accounting consolidation parents reported by the entity itself, not
beneficial owners, so edges use ``Relation.ACCOUNTING_PARENT`` and are labelled as such.
Confidence follows GLEIF's own corroboration level of the relationship record.
"""

from __future__ import annotations

import re

from tracer import names
from tracer.models import (
    Confidence, Edge, Entity, EntityKind, Evidence, OwnersResult, Relation, ReviewItem, Terminal, Unknown,
)
from tracer.sources.base import Candidate, LookupResult, Source, failure

API = "https://api.gleif.org/api/v1"
LEI_RE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
# Registration authority code of Companies House in GLEIF's RA list; registeredAs is then
# the UK company number, which joins GLEIF to Companies House without a name match.
RA_COMPANIES_HOUSE = {"RA000585"}

# reporting-exception reason -> (terminal, Korean note, needs review)
EXCEPTION_REASONS = {
    # Natural persons are not named in GLEIF, so this stays an open end ("추가 확인 필요"),
    # not an individual terminal.
    "NATURAL_PERSONS": (Terminal.DATA_END, "개인이 지배한다고 보고(이름 비공개), 추가 확인 필요", True),
    "NO_KNOWN_PERSON": (Terminal.DISPERSED, "지배하는 개인·법인이 없다고 보고", False),
    "NON_CONSOLIDATING": (Terminal.DATA_END, "모회사가 연결재무제표를 작성하지 않는다고 보고, 소유자는 미확인", True),
    "NON_PUBLIC": (Terminal.DATA_END, "모회사 정보 비공개 사유 보고", True),
    "BINDING_LEGAL_COMMITMENTS": (Terminal.DATA_END, "법적 약정으로 모회사 비공개", True),
    "LEGAL_OBSTACLES": (Terminal.DATA_END, "법적 장애로 모회사 비공개", True),
    "DISCLOSURE_DETRIMENTAL": (Terminal.DATA_END, "공개 시 불이익을 이유로 모회사 비공개", True),
    "DETRIMENT_NOT_EXCLUDED": (Terminal.DATA_END, "불이익 가능성을 이유로 모회사 비공개", True),
    "CONSENT_NOT_OBTAINED": (Terminal.DATA_END, "모회사 동의를 얻지 못해 비공개", True),
}


def record_url(lei: str) -> str:
    return f"https://search.gleif.org/#/record/{lei}"


def entity_from_record(rec: dict) -> Entity:
    a = rec["attributes"]
    ent = a["entity"]
    lei = a.get("lei") or rec["id"]
    ids = {"lei": lei}
    ra = (ent.get("registeredAt") or {}).get("id")
    reg_as = ent.get("registeredAs")
    if ra in RA_COMPANIES_HOUSE and reg_as:
        ids["ch"] = reg_as.strip().upper().zfill(8)
    if (ent.get("jurisdiction") or "").startswith("KR") and reg_as and re.fullmatch(r"\d{3}-?\d{2}-?\d{5}", reg_as):
        ids["brn"] = reg_as.replace("-", "")  # 사업자등록번호
    other = [n.get("name", "") for n in ent.get("otherNames") or []]
    if other:
        ids["alt_name"] = other[0]
    return Entity(id=f"lei:{lei}", name=ent["legalName"]["name"], kind=EntityKind.COMPANY,
                  jurisdiction=ent.get("jurisdiction") or (ent.get("legalAddress") or {}).get("country", ""),
                  identifiers=ids)


class GleifSource(Source):
    name = "gleif"
    label_ko = "GLEIF"

    def handles(self, entity: Entity) -> bool:
        return "lei" in entity.identifiers

    # ----------------------------------------------------------------- lookups
    def get(self, lei: str) -> Entity | None:
        r = self.http.get(f"{API}/lei-records/{lei}")
        return entity_from_record(r.json()["data"]) if r.status == 200 else None

    def by_registration(self, number: str) -> LookupResult:
        """Look up by registeredAs, e.g. a Korean business registration number."""
        out = LookupResult()
        variants = {number, re.sub(r"^(\d{3})(\d{2})(\d{5})$", r"\1-\2-\3", number.replace("-", ""))}
        try:
            for v in variants:
                r = self.http.get(f"{API}/lei-records", params={"filter[entity.registeredAs]": v})
                for rec in r.json().get("data", []) if r.status == 200 else []:
                    out.candidates.append(Candidate(entity_from_record(rec), 100.0, v))
                if out.candidates:
                    break
        except Exception as exc:  # noqa: BLE001
            out.unknowns.append(failure(self.name, self.label_ko, exc))
        return out

    def search(self, name: str) -> LookupResult:
        out = LookupResult()
        try:
            r = self.http.get(f"{API}/lei-records", params={"filter[fulltext]": name, "page[size]": 10})
            for rec in r.json().get("data", []) if r.status == 200 else []:
                e = entity_from_record(rec)
                s = max(names.score(name, e.name), names.score(name, e.identifiers.get("alt_name", "")))
                out.candidates.append(Candidate(e, s, e.name))
        except Exception as exc:  # noqa: BLE001
            out.unknowns.append(failure(self.name, self.label_ko, exc))
        out.candidates.sort(key=lambda c: -c.score)
        return out

    # ------------------------------------------------------------------ owners
    def _parent(self, lei: str, level: str) -> tuple[Entity | None, dict | None, dict | None]:
        """(parent entity, relationship record, reporting exception) for level direct|ultimate."""
        r = self.http.get(f"{API}/lei-records/{lei}/{level}-parent-relationship")
        if r.status == 200:
            rel = r.json()["data"]
            end = rel["attributes"]["relationship"]["endNode"]["id"]
            return self.get(end), rel, None
        r = self.http.get(f"{API}/lei-records/{lei}/{level}-parent-reporting-exception")
        if r.status == 200:
            return None, None, r.json()["data"]
        return None, None, None

    def owners(self, entity: Entity) -> OwnersResult:
        res = OwnersResult(source=self.name)
        lei = entity.identifiers.get("lei")
        if not lei:
            return res
        try:
            parent, rel, exc_rec = self._parent(lei, "direct")
            if parent is not None:
                res.entities.append(parent)
                res.edges.append(self._edge(parent, entity, rel, Relation.ACCOUNTING_PARENT))
                if res.edges[-1].confidence != Confidence.CONFIRMED:
                    res.review.append(ReviewItem(
                        "gleif_self_reported", f"{entity.name}의 모회사 {parent.name}: GLEIF 자기 보고(교차 검증 안 됨)",
                        f"{parent.id}->{entity.id}", 2, [record_url(lei)]))
                # The ultimate parent is recorded as a label only; the chain itself is walked
                # through direct parents.
                ult, ult_rel, _ = self._parent(lei, "ultimate")
                if ult is not None and ult.id != parent.id:
                    res.entities.append(ult)
                    res.edges.append(self._edge(ult, entity, ult_rel, Relation.ULTIMATE_ACCOUNTING_PARENT))
            elif exc_rec is not None:
                reason = exc_rec["attributes"].get("reason", "")
                terminal, note, review = EXCEPTION_REASONS.get(
                    reason, (Terminal.DATA_END, "모회사 미보고 사유 기록", True))
                res.terminal, res.terminal_note = terminal, f"GLEIF: {note}"
                res.unknowns.append(Unknown("gleif_reporting_exception", f"GLEIF: {note}", reason))
                if review:
                    res.review.append(ReviewItem("gleif_exception", f"{entity.name}: {note}", entity.id, 2, [record_url(lei)]))
            else:
                res.terminal, res.terminal_note = Terminal.DATA_END, "GLEIF: 모회사 정보와 미보고 사유 모두 없음"
                res.unknowns.append(Unknown("gleif_no_parent_data", "GLEIF에 모회사 정보가 없음"))
        except Exception as exc:  # noqa: BLE001
            res.unknowns.append(failure(self.name, self.label_ko, exc))
        return res

    def _edge(self, parent: Entity, child: Entity, rel: dict, relation: Relation) -> Edge:
        attrs = rel["attributes"]
        reg = attrs.get("registration") or {}
        corroborated = reg.get("corroborationLevel") == "FULLY_CORROBORATED"
        periods = attrs["relationship"].get("periods") or []
        acct = next((p for p in periods if p.get("type") == "ACCOUNTING_PERIOD"), {})
        as_of = (acct.get("endDate") or reg.get("lastUpdateDate") or "")[:10]
        lei = child.identifiers["lei"]
        return Edge(
            owner_id=parent.id, owned_id=child.id, relation=relation,
            confidence=Confidence.CONFIRMED if corroborated else Confidence.ESTIMATED,
            evidence=[Evidence(
                source=self.name, title=f"GLEIF relationship record ({attrs['relationship']['type']})",
                url=record_url(lei), as_of=as_of,
                locator=f"corroborationLevel={reg.get('corroborationLevel', '')}",
                quote=f"{lei} {attrs['relationship']['type']} {parent.identifiers['lei']}",
            )],
            note="회계상 연결 관계, 실질 소유와 다를 수 있음",
        )
