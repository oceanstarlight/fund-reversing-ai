"""Event root-cause tracer (보조 모듈, README §4).

Given a held company and an event date, collect DART filings around that date, tag them with
keyword rules and rank them as cause candidates. It never states a cause; each candidate keeps
its filing link. An ownership-change filing triggers a fresh ownership trace.

Current scope: DART filings only. News sources and automatic anomaly detection (price moves)
are later steps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from tracer import config
from tracer.models import OwnershipGraph, Unknown
from tracer.sources.dart import API, viewer_url

# (category, Korean label, pattern on report_nm, base weight). First match wins.
RULES = [
    ("ownership_change", "최대주주 변경", r"최대주주\s*변경|최대주주등\s*소유주식\s*변동|경영권\s*변경", 1.0),
    ("large_holding", "대량 보유 변동", r"주식등의\s*대량보유상황보고서|임원ㆍ주요주주특정증권등소유상황", 0.7),
    ("correction", "공시 정정", r"^\[기재정정\]|^\[첨부정정\]|정정", 0.5),
    ("trading_halt", "매매 정지·관리", r"매매거래정지|관리종목|상장적격성|상장폐지", 0.9),
    ("audit", "감사 의견", r"감사보고서|감사의견|계속기업", 0.8),
    ("legal", "소송·횡령·배임", r"소송|횡령|배임|회생|파산", 0.8),
    ("capital", "자본 변동", r"유상증자|무상증자|전환사채|신주인수권|감자|자기주식", 0.6),
    ("mna", "합병·분할·양수도", r"합병|분할|영업양수|영업양도|주식교환|공개매수", 0.8),
    ("earnings", "실적", r"매출액또는손익구조|영업실적|잠정", 0.4),
]
OWNERSHIP_CATEGORIES = {"ownership_change", "large_holding"}


@dataclass
class CauseCandidate:
    category: str
    label_ko: str
    title: str
    filed: str  # YYYYMMDD
    url: str
    filer: str
    score: float  # rule weight discounted by distance from the event date; ranking only


@dataclass
class RootCauseResult:
    corp_code: str
    event_date: str
    candidates: list[CauseCandidate] = field(default_factory=list)
    retrace: bool = False
    graph: OwnershipGraph | None = None
    unknowns: list[Unknown] = field(default_factory=list)


def tag(report_nm: str) -> tuple[str, str, float] | None:
    for cat, label, pat, w in RULES:
        if re.search(pat, report_nm):
            return cat, label, w
    return None


def rank(rows: list[dict], event: date) -> list[CauseCandidate]:
    out = []
    for r in rows:
        t = tag(r.get("report_nm", ""))
        if t is None:
            continue
        filed = r.get("rcept_dt", "")
        try:
            gap = abs((date(int(filed[:4]), int(filed[4:6]), int(filed[6:8])) - event).days)
        except ValueError:
            gap = 30
        out.append(CauseCandidate(t[0], t[1], r.get("report_nm", "").strip(), filed, viewer_url(r.get("rcept_no", "")),
                                  r.get("flr_nm", ""), round(t[2] / (1 + gap / 7), 4)))
    return sorted(out, key=lambda c: -c.score)


def trace_event(tracer, corp_code: str, event: date, days_before: int = 30, days_after: int = 3,
                retrace: bool = True) -> RootCauseResult:
    """``tracer`` is an ``orchestrator.Tracer``; reused for its HTTP client and DART key."""
    res = RootCauseResult(corp_code, event.isoformat())
    if not config.env("DART_API_KEY"):
        res.unknowns.append(Unknown("dart_unavailable", "DART 키가 없어 공시를 조회하지 못함"))
        return res
    params = {"crtfc_key": config.env("DART_API_KEY"), "corp_code": corp_code, "page_count": 100,
              "bgn_de": (event - timedelta(days=days_before)).strftime("%Y%m%d"),
              "end_de": (event + timedelta(days=days_after)).strftime("%Y%m%d")}
    try:
        d = tracer.http.get(f"{API}/list.json", params=params, ttl_hours=1).json()
    except Exception as exc:  # noqa: BLE001
        res.unknowns.append(Unknown("dart_error", "DART 공시 목록 조회 실패", f"{type(exc).__name__}: {exc}"[:200]))
        return res
    if d.get("status") not in ("000", "013"):
        res.unknowns.append(Unknown("dart_api_status", "DART가 오류 상태를 반환함", f"{d.get('status')}: {d.get('message', '')}"))
    res.candidates = rank(d.get("list") or [], event)
    if not res.candidates:
        res.unknowns.append(Unknown("no_candidates", "기간 내 규칙에 맞는 공시가 없음, 뉴스 등 다른 원인 확인 필요"))
    res.unknowns.append(Unknown("news_not_covered", "뉴스 소스는 아직 연결하지 않음"))
    if retrace and any(c.category in OWNERSHIP_CATEGORIES for c in res.candidates):
        res.retrace = True
        res.graph = tracer.trace(f"dart:{corp_code}")
    return res
