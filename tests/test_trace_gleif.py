"""GLEIF-only paths: unsupported jurisdictions, accounting parents, reporting exceptions."""

from tests.fakes import GLEIF, FakeHttp, exception, lei_record, relationship
from tracer.models import Confidence, Relation, Terminal
from tracer.orchestrator import Tracer

CHILD, PARENT, TOP = "CHILD00000000000JP01", "PARENT0000000000KY02", "TOP00000000000000NL3"


def _http(top_reason="NON_CONSOLIDATING", corroborated=True):
    h = FakeHttp()
    for lei, name, j in ((CHILD, "Child KK", "JP"), (PARENT, "Parent Cayman Ltd", "KY"), (TOP, "Top Holding BV", "NL")):
        h.add(f"{GLEIF}/lei-records/{lei}", {"data": lei_record(lei, name, j)})
    h.add(f"{GLEIF}/lei-records/{CHILD}/direct-parent-relationship", relationship(CHILD, PARENT, corroborated=corroborated))
    h.add(f"{GLEIF}/lei-records/{CHILD}/ultimate-parent-relationship", relationship(CHILD, TOP, "ULTIMATELY"))
    h.add(f"{GLEIF}/lei-records/{PARENT}/direct-parent-relationship", relationship(PARENT, TOP))
    h.add(f"{GLEIF}/lei-records/{PARENT}/ultimate-parent-relationship", relationship(PARENT, TOP, "ULTIMATELY"))
    h.add(f"{GLEIF}/lei-records/{TOP}/direct-parent-reporting-exception", exception(TOP, top_reason))
    return h


def test_unsupported_jurisdictions_walk_accounting_parents():
    g = Tracer(_http()).trace(CHILD)
    child, top = g.entities[f"lei:{CHILD}"], g.entities[f"lei:{TOP}"]
    assert child.unsupported_jurisdiction
    assert any(u.code == "unsupported_jurisdiction" for u in g.unknowns)
    rels = {(e.owner_id, e.relation) for e in g.owners_of(child.id)}
    assert (f"lei:{PARENT}", Relation.ACCOUNTING_PARENT) in rels
    # The ultimate parent is recorded as a label, not as a second walk.
    assert (f"lei:{TOP}", Relation.ULTIMATE_ACCOUNTING_PARENT) in rels
    # Top reports a non-consolidating parent: shown as the ultimate accounting parent, not an owner.
    assert top.terminal == Terminal.ULTIMATE_PARENT
    assert any(r.target == top.id for r in g.review)


def test_no_known_person_means_dispersed():
    g = Tracer(_http("NO_KNOWN_PERSON")).trace(CHILD)
    assert g.entities[f"lei:{TOP}"].terminal == Terminal.DISPERSED


def test_self_reported_parent_is_estimated():
    g = Tracer(_http(corroborated=False)).trace(CHILD)
    edge = next(e for e in g.owners_of(f"lei:{CHILD}") if e.relation == Relation.ACCOUNTING_PARENT)
    assert edge.confidence == Confidence.ESTIMATED
    assert any(r.code == "gleif_self_reported" for r in g.review)


def test_ambiguous_name_asks_a_person():
    h = FakeHttp()
    h.add(f"{GLEIF}/lei-records", {"data": [lei_record("A" * 18 + "01", "ACME LIMITED", "GB"),
                                            lei_record("B" * 18 + "02", "ACME LIMITED", "IE")]},
          {"filter[fulltext]": "Acme Limited"})
    g = Tracer(h).trace("Acme Limited")
    assert g.root_id == "" and len(g.candidates) == 2
    assert g.review[0].code == "input_unresolved"
