"""SEC EDGAR (no key, contact User-Agent required): issuers and Schedule 13D/13G holders.

Schedule 13D/13G are filed by holders of more than five percent of a class. Since the
structured (XML) schedules took effect, the reporting persons and percent of class can be read
directly; older text filings are listed for human review instead of being parsed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date

from tracer import config, names
from tracer.models import (
    Confidence, Edge, Entity, EntityKind, Evidence, OwnersResult, Relation, ReviewItem, Terminal, Unknown,
)
from tracer.sources.base import Candidate, LookupResult, Source, failure

TICKERS = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
FORMS_XML = {"SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A"}
FORMS_TEXT = {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}
# Type-of-reporting-person codes (Schedule 13D/G cover page).
_TYPE_KIND = {
    "IN": EntityKind.INDIVIDUAL, "IA": EntityKind.FUND, "IV": EntityKind.FUND, "IC": EntityKind.FUND,
    "BK": EntityKind.COMPANY, "BD": EntityKind.COMPANY, "CO": EntityKind.COMPANY, "HC": EntityKind.COMPANY,
    "PN": EntityKind.COMPANY, "EP": EntityKind.FUND, "OO": EntityKind.UNKNOWN,
}


def pad(cik: str | int) -> str:
    return str(int(cik)).zfill(10)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_schedule_xml(xml_bytes: bytes) -> list[dict]:
    """Reporting persons from a structured Schedule 13D/13G: name, percent text, type, citizenship.

    Finds every element that has a ``reportingPersonName`` child, so it reads both the 13G cover
    page layout and the 13D one without relying on exact parent tag names.
    """
    root = ET.fromstring(xml_bytes)
    out = []
    for el in root.iter():
        kids = {_local(c.tag): c for c in el}
        if "reportingPersonName" not in kids:
            continue
        def txt(*keys):
            for k in keys:
                if k in kids and (kids[k].text or "").strip():
                    return kids[k].text.strip()
            # percent and type can sit one level deeper
            for c in el.iter():
                if _local(c.tag) in keys and (c.text or "").strip():
                    return c.text.strip()
            return ""
        out.append({
            "name": txt("reportingPersonName"),
            "percent": txt("classPercent", "percentOfClass"),
            "type": txt("typeOfReportingPerson"),
            "citizenship": txt("citizenshipOrOrganization"),
        })
    return out


class SecSource(Source):
    name = "sec"
    label_ko = "SEC EDGAR"

    def __init__(self, http, today: date | None = None):
        super().__init__(http)
        self._tickers: list[dict] | None = None
        self._today = today

    def available(self) -> tuple[bool, str]:
        ua = config.env("SEC_USER_AGENT")
        return (True, "") if ua and "@" in ua else (False, "연락처가 담긴 사용자 에이전트 설정이 없어 조회하지 못함")

    def handles(self, entity: Entity) -> bool:
        return "cik" in entity.identifiers

    def _get(self, url: str):
        return self.http.get(url, headers={"User-Agent": config.env("SEC_USER_AGENT"), "Accept-Encoding": "gzip, deflate"})

    def tickers(self) -> list[dict]:
        if self._tickers is None:
            self._tickers = list(self._get(TICKERS).json().values())
        return self._tickers

    def get(self, cik: str | int) -> Entity | None:
        r = self._get(SUBMISSIONS.format(cik=pad(cik)))
        if r.status != 200:
            return None
        d = r.json()
        return self._entity(cik, d.get("name", ""), d.get("stateOfIncorporation", ""))

    def _entity(self, cik, name: str, state: str = "") -> Entity:
        juris = f"US-{state}" if re.fullmatch(r"[A-Z]{2}", state or "") else "US"
        ids = {"cik": pad(cik)}
        return Entity(id=f"cik:{pad(cik)}", name=name, kind=EntityKind.COMPANY, jurisdiction=juris, identifiers=ids)

    def search(self, name: str) -> LookupResult:
        out = LookupResult()
        ok, _ = self.available()
        if not ok:
            out.unknowns.append(self.unavailable_unknown())
            return out
        try:
            pairs = [(t["title"], t) for t in self.tickers()]
            scored = sorted(((names.score(name, n), t) for n, t in pairs), key=lambda x: -x[0])[:5]
            out.candidates = [Candidate(self._entity(t["cik_str"], t["title"]), s, t["title"]) for s, t in scored if s > 0]
        except Exception as exc:  # noqa: BLE001
            out.unknowns.append(failure(self.name, self.label_ko, exc))
        return out

    def owners(self, entity: Entity) -> OwnersResult:
        res = OwnersResult(source=self.name)
        ok, _ = self.available()
        if not ok:
            res.unknowns.append(self.unavailable_unknown())
            return res
        cik = entity.identifiers["cik"]
        try:
            sub = self._get(SUBMISSIONS.format(cik=cik)).json()
        except Exception as exc:  # noqa: BLE001
            res.unknowns.append(failure(self.name, self.label_ko, exc))
            return res
        filings = self._schedule_filings(sub)
        if not filings:
            res.unknowns.append(Unknown("sec_no_13dg", "최근 기간에 대량 보유 공시가 없음"))
        holders: dict[str, tuple[dict, dict]] = {}
        opened = 0
        for f in filings:  # newest first
            if f["form"] in FORMS_TEXT:
                res.review.append(ReviewItem("sec_text_13dg", f"{entity.name}: 구형식 {f['form']} ({f['date']}) 미파싱, 원문 확인 필요",
                                             entity.id, 3, [f["url"]]))
                continue
            if opened >= config.SEC_MAX_DOCS:
                res.unknowns.append(Unknown("sec_doc_limit", "열람 문서 수 제한으로 일부 대량 보유 공시를 읽지 않음"))
                break
            opened += 1
            try:
                persons = parse_schedule_xml(self._get(f["raw_url"]).content)
            except Exception as exc:  # noqa: BLE001
                res.unknowns.append(failure(self.name, self.label_ko, exc))
                continue
            for p in persons:
                key = names.normalize(p["name"])
                if key and key not in holders:  # keep the newest filing per holder
                    holders[key] = (p, f)
        top = 0.0
        for p, f in holders.values():
            pct = _pct(p["percent"])
            if pct is not None and pct < config.FOLLOW_MIN_PCT:
                continue  # holder fell below the reporting threshold (exit filing)
            top = max(top, pct or 0.0)
            kind = _TYPE_KIND.get(p["type"].split(",")[0].strip().upper(), names.classify_holder(p["name"]))
            pid = f"person:{names.slug(p['name'])}" if kind == EntityKind.INDIVIDUAL else f"name:{names.slug(p['name'])}"
            owner = Entity(id=pid, name=p["name"], kind=kind, jurisdiction="",
                           identifiers={"registered_in": p["citizenship"]} if p["citizenship"] else {})
            res.entities.append(owner)
            res.edges.append(Edge(owner.id, entity.id, Relation.BENEFICIAL_OWNER, Confidence.CONFIRMED,
                                  share_text=p["percent"], share_pct=pct, note=f"{f['form']} · 유형 {p['type'] or '-'}",
                                  evidence=[Evidence(self.name, f"{f['form']} filed {f['date']}", f["url"], as_of=f["event_date"] or f["date"],
                                                     locator="Cover page: percent of class", quote=f"{p['name']} | {p['percent']} | {p['type']}")]))
        if holders and top < config.CONTROL_PCT and all("13G" in f["form"] for _, f in holders.values()):
            res.terminal, res.terminal_note = Terminal.DISPERSED, "SEC: 수동적 대량 보유자(13G)만 있고 지배 지분 보유자 없음"
        return res

    def _schedule_filings(self, sub: dict) -> list[dict]:
        r = sub.get("filings", {}).get("recent", {})
        today = self._today or date.today()
        cutoff = str(today.replace(year=today.year - config.SEC_LOOKBACK_YEARS))
        cik_int = str(int(sub.get("cik", "0") or 0))
        out = []
        for i, form in enumerate(r.get("form", [])):
            if form not in FORMS_XML | FORMS_TEXT or r["filingDate"][i] < cutoff:
                continue
            acc = r["accessionNumber"][i].replace("-", "")
            doc = r["primaryDocument"][i]
            out.append({
                "form": form, "date": r["filingDate"][i], "event_date": (r.get("reportDate") or [""] * (i + 1))[i],
                "url": ARCHIVE.format(cik=cik_int, acc=acc, doc=doc),
                "raw_url": ARCHIVE.format(cik=cik_int, acc=acc, doc=doc.split("/")[-1]),
            })
        out.sort(key=lambda f: f["date"], reverse=True)
        return out


def _pct(text: str) -> float | None:
    t = (text or "").replace("%", "").strip()
    return float(t) if re.fullmatch(r"\d+(?:\.\d+)?", t) else None
