"""End-to-end trace: Korean listed company -> DART chain -> UK holder -> PSC individual."""

from datetime import date

import pytest

from tests.fakes import CH, DART, GLEIF, FakeHttp, corpcode_zip, exception, hyslr, lei_record
from tracer.models import Confidence, Relation, Terminal
from tracer.orchestrator import Tracer
from tracer.report import to_dot, to_markdown

OMEGA_LEI = "OMEGA0000000000000A1"


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "k")
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "k")
    h = FakeHttp()
    h.add(f"{DART}/corpCode.xml", corpcode_zip([
        {"corp_code": "00000001", "corp_name": "알파전자", "stock_code": "111111"},
        {"corp_code": "00000002", "corp_name": "베타홀딩스", "stock_code": ""},
        {"corp_code": "00000003", "corp_name": "감마물산", "stock_code": "333333"},
    ]))
    h.add(f"{DART}/hyslrSttus.json", hyslr([("베타홀딩스(주)", "본인", "30.50"), ("홍길동", "친인척", "5.20"),
                                            ("감마물산", "계열회사", "3.00"), ("계", "", "38.70")]), {"corp_code": "00000001"})
    h.add(f"{DART}/hyslrSttus.json", hyslr([("Omega Holdings Limited", "본인", "60.00"), ("감마물산", "계열회사", "10.00")]),
          {"corp_code": "00000002"})
    h.add(f"{DART}/hyslrSttus.json", hyslr([("알파전자", "계열회사", "12.00")]), {"corp_code": "00000003"})
    h.add(f"{DART}/company.json", {"status": "000", "bizr_no": "1112233333"}, {"corp_code": "00000001"})
    # Omega: found by Companies House search, joined to GLEIF by name.
    h.add(f"{CH}/search/companies", {"items": [{"company_number": "1234567", "title": "OMEGA HOLDINGS LIMITED"}]})
    h.add(f"{CH}/company/01234567/persons-with-significant-control", {"items": [
        {"kind": "individual-person-with-significant-control", "name": "Ms Jane Doe", "notified_on": "2024-02-01",
         "natures_of_control": ["ownership-of-shares-75-to-100-percent", "voting-rights-75-to-100-percent"]}]})
    for q in ("OMEGA HOLDINGS LIMITED", "Omega Holdings Limited"):
        h.add(f"{GLEIF}/lei-records", {"data": [lei_record(OMEGA_LEI, "OMEGA HOLDINGS LIMITED", "GB", "RA000585", "01234567")]},
              {"filter[fulltext]": q})
    h.add(f"{GLEIF}/lei-records/{OMEGA_LEI}/direct-parent-reporting-exception", exception(OMEGA_LEI, "NATURAL_PERSONS"))
    return h


def test_full_chain(http):
    g = Tracer(http, today=date(2026, 10, 17)).trace("알파전자")
    root = g.entities[g.root_id]
    assert root.name == "알파전자"
    # Largest holder followed, minority affiliate below threshold recorded but not followed first.
    names_up = {g.entities[e.owner_id].name for e in g.owners_of(root.id)}
    assert {"베타홀딩스", "홍길동", "감마물산"} <= names_up
    beta_edge = next(e for e in g.owners_of(root.id) if g.entities[e.owner_id].name == "베타홀딩스")
    assert beta_edge.share_pct == 30.5 and beta_edge.confidence == Confidence.CONFIRMED
    assert beta_edge.evidence[0].url.endswith("rcpNo=20261114000001")
    assert "crtfc_key" not in beta_edge.evidence[0].url
    # UK holder linked to Companies House and GLEIF, PSC individual is the terminal.
    omega = g.entities["ch:01234567"]
    assert omega.identifiers.get("lei") == OMEGA_LEI
    psc = [e for e in g.owners_of(omega.id) if e.relation == Relation.PSC]
    assert psc and psc[0].share_text.startswith("주식 75~100%") and psc[0].share_pct is None
    jane = g.entities[psc[0].owner_id]
    assert jane.terminal == Terminal.INDIVIDUAL
    # 알파 -> 감마 -> 베타 -> 알파 is a cycle.
    assert any(set(f.path) >= {"dart:00000001", "dart:00000002", "dart:00000003"} for f in g.flags)
    assert any(r.code.startswith("flag_") for r in g.review)
    # Renderers work on the result.
    assert "digraph" in to_dot(g)
    md = to_markdown(g)
    assert "사람 검토 목록" in md and "Ms Jane Doe" in md


def test_missing_key_is_reported_not_raised(http, monkeypatch):
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY")
    g = Tracer(http, today=date(2026, 10, 17)).trace("알파전자")
    assert any(u.code == "companies_house_unavailable" for u in g.unknowns)
    # Omega is still found through GLEIF, which carries its UK company number.
    omega = g.entities[g.canonical(f"lei:{OMEGA_LEI}")]
    assert omega.identifiers["ch"] == "01234567"


def test_unit_messages_have_no_digits(http):
    g = Tracer(http, today=date(2026, 10, 17)).trace("알파전자")
    assert all(not any(ch.isdigit() for ch in u.message) for u in g.unknowns)
