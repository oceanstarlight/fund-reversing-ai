"""Live connectivity checks for each source: one cheap request per source.

Used by the app's "연결 점검" panel and by ``scripts/smoke.py`` (GitHub Actions, manual run).
Messages never include key values.
"""

from __future__ import annotations

from dataclasses import dataclass

from tracer import config
from tracer.orchestrator import Tracer

APPLE_CIK = "0000320193"
APPLE_LEI = "HWUPKR0MPOU8FGXBT394"
SAMSUNG_STOCK = "005930"


@dataclass
class Check:
    source: str
    ok: bool
    message: str


def run_checks(tracer: Tracer | None = None) -> list[Check]:
    t = tracer or Tracer()
    out = []

    def attempt(source: str, fn) -> None:
        try:
            ok, msg = fn()
        except Exception as exc:  # noqa: BLE001 - a check reports, never raises
            ok, msg = False, f"{type(exc).__name__}: {str(exc)[:150]}"
        out.append(Check(source, ok, msg))

    def gleif():
        e = t.gleif.get(APPLE_LEI)
        return (e is not None, f"LEI 조회 성공: {e.name}" if e else "LEI 조회 실패")

    def sec():
        ok, reason = t.sec.available()
        if not ok:
            return False, reason
        e = t.sec.get(APPLE_CIK)
        return (e is not None, f"submissions 조회 성공: {e.name}" if e else "submissions 조회 실패(User-Agent 확인)")

    def dart():
        ok, reason = t.dart.available()
        if not ok:
            return False, reason
        e = t.dart.by_code(SAMSUNG_STOCK)
        if e is None:
            return False, "고유번호 목록에서 종목코드를 찾지 못함"
        info = t.dart.company(e.identifiers["dart"])
        return (bool(info), f"기업개황 조회 성공: {e.name}" if info else "기업개황 조회 실패(키 확인)")

    def companies_house():
        ok, reason = t.ch.available()
        return (ok, f"색인 사용: {config.CH_INDEX_PATH.name} (PSC {t.ch.index.meta('psc_snapshot')})" if ok else f"{reason} (보류 중)")

    def llm_check():
        from tracer import llm
        p = llm.provider()
        return (p != "none", f"문서 추출용 LLM: {p} / {llm.model()}" if p != "none" else "LLM 키 없음 (문서 추출만 꺼짐)")

    attempt("GLEIF", gleif)
    attempt("SEC EDGAR", sec)
    attempt("DART", dart)
    attempt("Companies House", companies_house)
    attempt("LLM", llm_check)
    return out
