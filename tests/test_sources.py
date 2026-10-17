from datetime import date

from tests.fakes import FakeHttp, ch_index
from tracer.models import Entity, Terminal
from tracer.sources.ch_index import ChIndex
from tracer.sources.companies_house import CompaniesHouseSource, describe_control
from tracer.sources.dart import report_order
from tracer.sources.sec import SecSource, parse_schedule_xml

SCHEDULE_13G = b"""<?xml version="1.0"?><edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13g">
<formData><coverPageHeaderReportingPersonDetails>
<reportingPersonName>Big Asset Management LLC</reportingPersonName><citizenshipOrOrganization>DE</citizenshipOrOrganization>
<classPercent>8.1</classPercent><typeOfReportingPerson>IA</typeOfReportingPerson>
</coverPageHeaderReportingPersonDetails><coverPageHeaderReportingPersonDetails>
<reportingPersonName>Jane Founder</reportingPersonName><classPercent>12.4</classPercent><typeOfReportingPerson>IN</typeOfReportingPerson>
</coverPageHeaderReportingPersonDetails></formData></edgarSubmission>"""


def test_parse_schedule_xml():
    rows = parse_schedule_xml(SCHEDULE_13G)
    assert [(r["name"], r["percent"], r["type"]) for r in rows] == [
        ("Big Asset Management LLC", "8.1", "IA"), ("Jane Founder", "12.4", "IN")]


def test_sec_13g_only_holders_are_dispersed(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "test test@example.com")
    h = FakeHttp()
    h.add("https://data.sec.gov/submissions/CIK0000000042.json", {"cik": "42", "name": "Widget Corp", "filings": {"recent": {
        "form": ["SCHEDULE 13G", "SC 13G/A", "10-K"], "filingDate": ["2026-02-10", "2025-02-10", "2026-02-01"],
        "reportDate": ["2025-12-31", "", ""], "accessionNumber": ["0000000001-26-000001", "0000000002-25-000001", "x"],
        "primaryDocument": ["xslSCHEDULE_13G_X02/primary_doc.xml", "a.txt", "k.htm"]}}})
    h.add("https://www.sec.gov/Archives/edgar/data/42/000000000126000001/primary_doc.xml", SCHEDULE_13G)
    ent = Entity("cik:0000000042", "Widget Corp", jurisdiction="US-DE", identifiers={"cik": "0000000042"})
    res = SecSource(h, today=date(2026, 10, 17)).owners(ent)
    assert {e.share_text for e in res.edges} == {"8.1", "12.4"}
    assert res.terminal == Terminal.DISPERSED
    assert any(r.code == "sec_text_13dg" for r in res.review)  # old text filing left for a person
    assert "xsl" in res.edges[0].evidence[0].url  # human-readable rendering


def test_companies_house_no_psc_statement_is_dispersed(tmp_path):
    path = ch_index(tmp_path / "ch.sqlite", [("00000009", "X LIMITED")], [
        {"company_number": "00000009", "data": {"kind": "persons-with-significant-control-statement",
                                                "statement": "no-individual-or-entity-with-signficant-control"}}])
    res = CompaniesHouseSource(FakeHttp(), ChIndex(path)).owners(Entity("ch:00000009", "X Ltd", jurisdiction="GB", identifiers={"ch": "00000009"}))
    assert res.terminal == Terminal.DISPERSED


def test_describe_control_keeps_bands():
    assert describe_control(["ownership-of-shares-25-to-50-percent-as-trust", "right-to-appoint-and-remove-directors"]) \
        == "주식 25~50% / 이사 선임·해임권"


def test_dart_report_order_newest_first():
    order = report_order(date(2026, 10, 17))
    assert order[:4] == [("2026", "11014"), ("2026", "11012"), ("2026", "11013"), ("2025", "11011")]


def test_companies_house_corporate_psc_and_search(tmp_path):
    path = ch_index(tmp_path / "ch.sqlite", [("00000010", "CHILD LIMITED"), ("00000011", "PARENT HOLDINGS PLC")], [
        {"company_number": "00000010", "data": {
            "kind": "corporate-entity-person-with-significant-control", "name": "Parent Holdings plc",
            "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
            "identification": {"country_registered": "England & Wales", "registration_number": "11"}}}])
    src = CompaniesHouseSource(FakeHttp(), ChIndex(path))
    res = src.owners(Entity("ch:00000010", "CHILD LIMITED", jurisdiction="GB", identifiers={"ch": "00000010"}))
    assert res.entities[0].id == "ch:00000011"  # joined by registration number, not by name
    assert src.search("Parent Holdings PLC").candidates[0].entity.id == "ch:00000011"
    assert src.get("10").name == "CHILD LIMITED"
