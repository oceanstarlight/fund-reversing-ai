from tracer import names
from tracer.models import Confidence, EntityKind


def test_normalize_drops_legal_forms_but_keeps_holdings():
    assert names.normalize("삼성전자(주)") == names.normalize("삼성전자 주식회사") == "삼성전자"
    assert names.normalize("SAMSUNG ELECTRONICS CO., LTD") == "samsung electronics"
    assert names.normalize("Omega Holdings Limited") == "omega holdings"
    assert names.score("Omega Holdings Ltd", "Omega Limited") < 97


def test_match_confidence_bands():
    assert names.match_confidence(100) == Confidence.CONFIRMED
    assert names.match_confidence(90) == Confidence.ESTIMATED
    assert names.match_confidence(50) is None


def test_classify_holder():
    assert names.classify_holder("홍길동") == EntityKind.INDIVIDUAL
    assert names.classify_holder("국민연금공단") == EntityKind.PUBLIC_BODY
    assert names.classify_holder("삼성생명공익재단") == EntityKind.FOUNDATION
    assert names.classify_holder("BlackRock Fund Advisors") == EntityKind.FUND
    assert names.classify_holder("Omega Holdings Limited") == EntityKind.COMPANY


def test_parenthesized_alias_and_article():
    assert names.score("에쓰-오일", "에쓰-오일 (S-OIL) (주)") == 100
    assert names.score("S-Oil Corporation", "에쓰-오일 (S-OIL) (주)") == 100
    assert names.normalize("SHELL PETROLEUM COMPANY LIMITED(THE)") == names.normalize("The Shell Petroleum Company Limited")
