"""Name normalization, fuzzy matching and holder-type rules (deterministic, no LLM)."""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from tracer import config
from tracer.models import Confidence, EntityKind

# Legal-form tokens (and the article "the", which UK registers also write as a "(THE)" suffix)
# removed before comparing names. "Holdings" is kept on purpose: "X Holdings" and "X" are
# usually different entities of one group.
_LEGAL_FORMS = [
    r"주식회사", r"\(주\)", r"㈜", r"유한회사", r"\(유\)", r"유한책임회사", r"합자회사", r"재단법인", r"사단법인",
    r"public limited company", r"incorporated", r"corporation", r"company", r"limited",
    r"inc", r"corp", r"co", r"ltd", r"llc", r"llp", r"lp", r"plc", r"sa", r"ag", r"nv", r"bv", r"gmbh", r"pte", r"kk", r"the",
]
_FORM_RE = re.compile(r"(?<![a-z0-9])(?:" + "|".join(_LEGAL_FORMS) + r")(?![a-z0-9])", re.IGNORECASE)


def normalize(name: str) -> str:
    """Lowercase, NFKC, drop legal forms and punctuation."""
    s = unicodedata.normalize("NFKC", name or "").lower().replace("&", " and ")
    s = _FORM_RE.sub(" ", s)
    s = re.sub(r"[^\w가-힣 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def variants(name: str) -> set[str]:
    """Normalized forms of a name: as written, without parenthesized parts, and each
    parenthesized part alone ("에쓰-오일 (S-OIL) (주)" -> "에쓰 오일 s oil", "에쓰 오일", "s oil")."""
    raw = _FORM_RE.sub(" ", unicodedata.normalize("NFKC", name or "").lower())
    out = {normalize(name), normalize(re.sub(r"\([^)]*\)", " ", raw))}
    out.update(normalize(p) for p in re.findall(r"\(([^)]*)\)", raw))
    return {v for v in out if v}


def score(a: str, b: str) -> float:
    """Similarity 0-100 of two names: best match over their normalized variants."""
    best = 0.0
    for na in variants(a):
        for nb in variants(b):
            if na == nb:
                return 100.0
            best = max(best, float(fuzz.token_sort_ratio(na, nb)))
    return best


def match_confidence(s: float) -> Confidence | None:
    """Confidence of a link that rests on a name match; None means do not link."""
    if s >= config.NAME_MATCH_AUTO:
        return Confidence.CONFIRMED
    if s >= config.NAME_MATCH_REVIEW:
        return Confidence.ESTIMATED
    return None


def best_match(name: str, candidates: list[tuple[str, object]]) -> tuple[object | None, float]:
    """Highest-scoring (payload, score) among (candidate_name, payload) pairs."""
    best, best_s = None, 0.0
    for cand_name, payload in candidates:
        s = score(name, cand_name)
        if s > best_s:
            best, best_s = payload, s
    return best, best_s


def is_korean(text: str) -> bool:
    return bool(re.search(r"[가-힣]", text or ""))


_COMPANY_HINT = re.compile(
    r"주식회사|\(주\)|㈜|유한|홀딩스|지주|증권|은행|보험|캐피탈|투자|자산운용|파트너스|펀드|조합|"
    r"\b(inc|corp|corporation|co|ltd|limited|llc|llp|lp|plc|sa|ag|nv|bv|gmbh|pte|holdings?|"
    r"bank|fund|trust|capital|partners|group|n\.a)\b",
    re.IGNORECASE,
)
_FOUNDATION_HINT = re.compile(r"재단|장학회|복지회|foundation|stiftung|charitable", re.IGNORECASE)
_PUBLIC_HINT = re.compile(
    r"국민연금|공무원연금|사학연금|기획재정부|산업은행|한국은행|정부|공사$|공단$|"
    r"government|ministry|treasury|sovereign|pension fund|central bank",
    re.IGNORECASE,
)
_FUND_HINT = re.compile(r"자산운용|투자신탁|펀드|asset management|investment management|advisers?|funds?\b|etf",
                        re.IGNORECASE)


def classify_holder(name: str) -> EntityKind:
    """Rule-based holder type from a disclosed name. Short Hangul names without company
    markers are treated as individuals (DART lists people by name only)."""
    n = (name or "").strip()
    if _PUBLIC_HINT.search(n):
        return EntityKind.PUBLIC_BODY
    if _FOUNDATION_HINT.search(n):
        return EntityKind.FOUNDATION
    if _FUND_HINT.search(n):
        return EntityKind.FUND
    if _COMPANY_HINT.search(n):
        return EntityKind.COMPANY
    if is_korean(n) and len(n.replace(" ", "")) <= 4:
        return EntityKind.INDIVIDUAL
    if re.fullmatch(r"[A-Za-z][A-Za-z.'\- ]{1,40}", n) and 2 <= len(n.split()) <= 4:
        return EntityKind.INDIVIDUAL
    return EntityKind.UNKNOWN


def slug(name: str) -> str:
    return re.sub(r"\s+", "_", normalize(name)) or "unnamed"
