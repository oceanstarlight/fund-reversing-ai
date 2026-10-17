from datetime import date

from tracer import extract
from tracer.events.root_cause import rank, tag
from tracer.models import Confidence, Entity

DOC = "Section 3.1 Capital. Alpha GP Limited holds a 1% general partner interest. Beta Pension Fund holds 60% of the commitments."


def test_extract_keeps_only_quoted_values(monkeypatch):
    items = extract.Extraction(holdings=[
        extract.ExtractedHolding(holder_name="Beta Pension Fund", holder_type="fund", share_text="60%",
                                 quote="Beta Pension Fund holds 60% of the commitments.", page="3.1"),
        extract.ExtractedHolding(holder_name="Gamma Ltd", holder_type="company", share_text="39%",
                                 quote="Gamma Ltd holds 39%.", page=""),  # not in the text
    ])
    monkeypatch.setattr(extract.llm, "structured_call", lambda *a, **k: (items, None))
    ents, edges, review, unknowns = extract.extract_holdings(Entity("lei:X", "Fund I LP"), DOC, "LPA")
    assert [e.name for e in ents] == ["Beta Pension Fund"]
    assert edges[0].share_pct == 60.0 and edges[0].confidence == Confidence.ESTIMATED
    assert review and any(u.code == "extract_rejected" for u in unknowns)


def test_extract_without_llm_degrades():
    _, edges, _, unknowns = extract.extract_holdings(Entity("lei:X", "Fund I LP"), DOC, "LPA")
    assert edges == [] and unknowns[0].code == "extract_unavailable"


def test_root_cause_rules_rank_by_weight_and_date():
    assert tag("최대주주변경")[0] == "ownership_change"
    assert tag("[기재정정]사업보고서")[0] == "correction"
    rows = [{"report_nm": "주식등의대량보유상황보고서(일반)", "rcept_dt": "20260901", "rcept_no": "1"},
            {"report_nm": "최대주주변경", "rcept_dt": "20261010", "rcept_no": "2"},
            {"report_nm": "기업설명회(IR)개최", "rcept_dt": "20261010", "rcept_no": "3"}]
    out = rank(rows, date(2026, 10, 10))
    assert [c.category for c in out] == ["ownership_change", "large_holding"]
