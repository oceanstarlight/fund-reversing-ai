"""OpenDART (key required, free): corp code list, company overview, largest-shareholder table.

The starting point for Korean companies. ``owners`` reads 「최대주주 및 특수관계인의 주식소유
현황」 (hyslrSttus) from the newest periodic report that has it, and resolves corporate holders
against DART's own corp list. Holders with Latin-script names (usually foreign) are returned as
name-only entities; the orchestrator links them through GLEIF.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date

from rapidfuzz import fuzz, process

from tracer import config, names
from tracer.models import (
    Confidence, Edge, Entity, EntityKind, Evidence, OwnersResult, Relation, ReviewItem, Unknown,
)
from tracer.sources.base import Candidate, LookupResult, Source, failure

API = "https://opendart.fss.or.kr/api"
# Periodic report codes: 사업보고서, 3분기, 반기, 1분기.
REPORTS = {"11011": "사업보고서", "11014": "3분기보고서", "11012": "반기보고서", "11013": "1분기보고서"}
_INDIVIDUAL_RELATIONS = re.compile(r"친인척|배우자|자녀|형제|자매|부모|임원|친족|개인")
_COMPANY_RELATIONS = re.compile(r"계열|법인|회사|최대주주의\s*(?:모|자)회사")
_FOUNDATION_RELATIONS = re.compile(r"재단|비영리")


def viewer_url(rcept_no: str) -> str:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


def parse_pct(text: str) -> float | None:
    t = (text or "").replace(",", "").strip()
    return float(t) if re.fullmatch(r"\d+(?:\.\d+)?", t) else None


def report_order(today: date | None = None) -> list[tuple[str, str]]:
    """(business year, report code) pairs, newest first. The business report for year Y-1 is
    filed in spring of Y, so it ranks after Y's first-quarter report."""
    y = (today or date.today()).year
    out = []
    for year in range(y, y - config.DART_LOOKBACK_YEARS - 1, -1):
        out += [(str(year), "11014"), (str(year), "11012"), (str(year), "11013"), (str(year - 1), "11011")]
    return out


class DartSource(Source):
    name = "dart"
    label_ko = "DART"

    def __init__(self, http, today: date | None = None):
        super().__init__(http)
        self._corps: list[dict] | None = None
        self._today = today

    def available(self) -> tuple[bool, str]:
        return (True, "") if config.env("DART_API_KEY") else (False, "API 키가 없어 한국 공시를 조회하지 못함")

    def handles(self, entity: Entity) -> bool:
        return "dart" in entity.identifiers

    def _params(self, **kw) -> dict:
        return {"crtfc_key": config.env("DART_API_KEY"), **kw}

    # ---------------------------------------------------------------- corp list
    def corps(self) -> list[dict]:
        if self._corps is None:
            r = self.http.get(f"{API}/corpCode.xml", params=self._params(), ttl_hours=config.DART_CORPCODE_TTL_HOURS)
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                root = ET.fromstring(zf.read(zf.namelist()[0]))
            self._corps = [
                {c.tag: (c.text or "").strip() for c in item} for item in root.iter("list")
            ]
            for c in self._corps:
                c["_norm"] = names.normalize(c.get("corp_name", ""))
        return self._corps

    def _entity(self, corp: dict) -> Entity:
        ids = {"dart": corp["corp_code"]}
        if corp.get("stock_code"):
            ids["krx"] = corp["stock_code"]
        if corp.get("corp_eng_name"):
            ids["alt_name"] = corp["corp_eng_name"]
        return Entity(id=f"dart:{corp['corp_code']}", name=corp["corp_name"], kind=EntityKind.COMPANY,
                      jurisdiction="KR", identifiers=ids)

    def by_code(self, code: str) -> Entity | None:
        """6-digit KRX stock code or 8-digit DART corp code."""
        key = "stock_code" if len(code) == 6 else "corp_code"
        corp = next((c for c in self.corps() if c.get(key) == code), None)
        return self._entity(corp) if corp else None

    def search(self, name: str) -> LookupResult:
        out = LookupResult()
        ok, _ = self.available()
        if not ok:
            out.unknowns.append(self.unavailable_unknown())
            return out
        try:
            corps = self.corps()
            q = names.normalize(name)
            exact = [c for c in corps if c["_norm"] == q]
            if exact:
                # Prefer listed companies when several share a name.
                exact.sort(key=lambda c: (not c.get("stock_code"), c["corp_code"]))
                out.candidates = [Candidate(self._entity(c), 100.0, c["corp_name"]) for c in exact[:5]]
                return out
            hits = process.extract(q, [c["_norm"] for c in corps], scorer=fuzz.token_sort_ratio, limit=5)
            out.candidates = [Candidate(self._entity(corps[i]), float(s), corps[i]["corp_name"]) for _, s, i in hits]
        except Exception as exc:  # noqa: BLE001
            out.unknowns.append(failure(self.name, self.label_ko, exc))
        return out

    def company(self, corp_code: str) -> dict:
        r = self.http.get(f"{API}/company.json", params=self._params(corp_code=corp_code))
        d = r.json()
        return d if d.get("status") == "000" else {}

    def enrich(self, entity: Entity) -> None:
        """Add the business registration number (joins DART to GLEIF's registeredAs)."""
        try:
            info = self.company(entity.identifiers["dart"])
        except Exception:  # noqa: BLE001 - enrichment is optional
            return
        if info.get("bizr_no"):
            entity.identifiers.setdefault("brn", info["bizr_no"].replace("-", ""))
        if info.get("corp_name_eng"):
            entity.identifiers.setdefault("alt_name", info["corp_name_eng"])

    # ------------------------------------------------------------------ owners
    def largest_shareholders(self, corp_code: str) -> tuple[list[dict], str, str, list[Unknown]]:
        """(rows, business year, report code, unknowns) from the newest report that has the table."""
        unknowns: list[Unknown] = []
        for year, code in report_order(self._today):
            r = self.http.get(f"{API}/hyslrSttus.json", params=self._params(corp_code=corp_code, bsns_year=year, reprt_code=code))
            d = r.json()
            status = d.get("status")
            if status == "000" and d.get("list"):
                return d["list"], year, code, unknowns
            if status not in ("000", "013"):
                unknowns.append(Unknown("dart_api_status", "DART가 오류 상태를 반환해 조회를 중단함", f"{status}: {d.get('message', '')}"))
                break
        unknowns.append(Unknown("dart_no_report", "최근 정기보고서에서 최대주주 현황을 찾지 못함"))
        return [], "", "", unknowns

    def _holder_kind(self, row: dict) -> EntityKind:
        relate = row.get("relate", "") or ""
        kind = names.classify_holder(row.get("nm", ""))
        if _FOUNDATION_RELATIONS.search(relate) and kind != EntityKind.PUBLIC_BODY:
            return EntityKind.FOUNDATION
        if _COMPANY_RELATIONS.search(relate) and kind in (EntityKind.UNKNOWN, EntityKind.INDIVIDUAL):
            return EntityKind.COMPANY
        if _INDIVIDUAL_RELATIONS.search(relate) and kind == EntityKind.UNKNOWN:
            return EntityKind.INDIVIDUAL
        return kind

    def owners(self, entity: Entity) -> OwnersResult:
        res = OwnersResult(source=self.name)
        ok, _ = self.available()
        if not ok:
            res.unknowns.append(self.unavailable_unknown())
            return res
        corp_code = entity.identifiers["dart"]
        try:
            rows, year, code, unknowns = self.largest_shareholders(corp_code)
            res.unknowns += unknowns
            corps = self.corps()
            by_norm: dict[str, list[dict]] = {}
            for c in corps:
                by_norm.setdefault(c["_norm"], []).append(c)
        except Exception as exc:  # noqa: BLE001
            res.unknowns.append(failure(self.name, self.label_ko, exc))
            return res

        for row in rows:
            nm = (row.get("nm") or "").strip()
            stock_kind = row.get("stock_knd") or ""
            if not nm or nm in ("계", "합계", "소계") or (stock_kind and "보통" not in stock_kind):
                continue
            pct_text = (row.get("trmend_posesn_stock_qota_rt") or "").strip()
            owner, conf, note = self._resolve_holder(nm, row, by_norm, corps)
            res.entities.append(owner)
            ev = Evidence(
                source=self.name, title=f"{year} {REPORTS.get(code, code)} · 최대주주 및 특수관계인 주식소유 현황",
                url=viewer_url(row.get("rcept_no", "")), as_of=row.get("stlm_dt", ""),
                locator="최대주주 및 특수관계인의 주식소유 현황(기말)",
                quote=f"{nm} | {row.get('relate', '')} | {stock_kind} | {pct_text}",
            )
            res.edges.append(Edge(owner.id, entity.id, Relation.SHAREHOLDING, conf, share_text=pct_text,
                                  share_pct=parse_pct(pct_text), evidence=[ev],
                                  note=f"관계: {row.get('relate', '')}" + (f" · {note}" if note else "")))
            if conf != Confidence.CONFIRMED:
                res.review.append(ReviewItem("dart_name_match", f"'{nm}'을(를) {owner.name}(으)로 연결: 이름 유사도 기반",
                                             f"{owner.id}->{entity.id}", 2, [ev.url]))
        return res

    def _resolve_holder(self, nm: str, row: dict, by_norm: dict, corps: list[dict]) -> tuple[Entity, Confidence, str]:
        kind = self._holder_kind(row)
        if kind == EntityKind.INDIVIDUAL:
            return Entity(id=f"person:{names.slug(nm)}", name=nm, kind=kind, jurisdiction="KR"), Confidence.CONFIRMED, ""
        if kind in (EntityKind.FOUNDATION, EntityKind.PUBLIC_BODY):
            return Entity(id=f"name:{names.slug(nm)}", name=nm, kind=kind, jurisdiction="KR"), Confidence.CONFIRMED, ""
        norm = names.normalize(nm)
        if norm in by_norm:
            hit = sorted(by_norm[norm], key=lambda c: (not c.get("stock_code"), c["corp_code"]))[0]
            return self._entity(hit), Confidence.CONFIRMED, ""
        if names.is_korean(nm):
            found = process.extractOne(norm, [c["_norm"] for c in corps], scorer=fuzz.token_sort_ratio)
            if found:
                _, s, i = found
                conf = names.match_confidence(float(s))
                if conf is not None:
                    return self._entity(corps[i]), Confidence.ESTIMATED, f"이름 매칭 '{corps[i]['corp_name']}'"
        # Unresolved here: name-only entity, linked later by the orchestrator (e.g. via GLEIF).
        return (Entity(id=f"name:{names.slug(nm)}", name=nm, kind=kind, jurisdiction="" if not names.is_korean(nm) else "KR"),
                Confidence.CONFIRMED, "")
