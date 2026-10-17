"""Document extraction (LPA, PPM, shareholder agreements): the only LLM role.

The model proposes holdings together with a verbatim quote. Code then keeps only items whose
quote appears in the document and whose share text appears in the quote, so every value shown
comes from the original text. Results are always ``ESTIMATED`` and go to human review.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from tracer import llm, names
from tracer.models import Confidence, Edge, Entity, EntityKind, Evidence, Relation, ReviewItem, Unknown


class ExtractedHolding(BaseModel):
    holder_name: str = Field(description="Holder exactly as written in the document")
    holder_type: str = Field(description="company | individual | fund | foundation | public_body | unknown")
    share_text: str = Field(description="Share, interest or control wording exactly as written, e.g. '60%' or 'general partner'")
    quote: str = Field(description="Verbatim sentence from the document containing holder_name and share_text")
    page: str = Field(description="Page or section label if visible in the text, else empty")


class Extraction(BaseModel):
    holdings: list[ExtractedHolding]


SYSTEM = (
    "You extract ownership and control statements about one target entity from a fund or "
    "corporate document. Return only holders of the target that the text states explicitly. "
    "Copy names, share wording and the supporting sentence verbatim; never compute, round or "
    "infer a figure. If the text states nothing, return an empty list."
)


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def verify(item: ExtractedHolding, text: str) -> list[str]:
    """Problems that make an extracted item unusable; empty means it is quoted from the text."""
    problems = []
    doc, quote = _squash(text), _squash(item.quote)
    if not quote or quote not in doc:
        problems.append("quote not found in document")
    if item.share_text and _squash(item.share_text) not in quote:
        problems.append("share text not inside quote")
    if _squash(item.holder_name) not in quote:
        problems.append("holder name not inside quote")
    return problems


def extract_holdings(target: Entity, text: str, doc_title: str, doc_url: str = ""
                     ) -> tuple[list[Entity], list[Edge], list[ReviewItem], list[Unknown]]:
    user = f"TARGET: {target.name}\n\nDOCUMENT:\n{text[:60000]}"
    out, err = llm.structured_call(Extraction, SYSTEM, user)
    if out is None:
        return [], [], [], [Unknown("extract_unavailable", "문서 추출을 실행하지 못함", err or "")]
    ents, edges, review, unknowns = [], [], [], []
    kinds = {k.value: k for k in EntityKind}
    for it in out.holdings:
        problems = verify(it, text)
        if problems:
            unknowns.append(Unknown("extract_rejected", "원문에서 확인되지 않는 추출 결과를 버림", f"{it.holder_name}: {problems}"))
            continue
        kind = kinds.get(it.holder_type, EntityKind.UNKNOWN)
        prefix = "person" if kind == EntityKind.INDIVIDUAL else "name"
        owner = Entity(id=f"{prefix}:{names.slug(it.holder_name)}", name=it.holder_name, kind=kind)
        pct = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", it.share_text)
        ents.append(owner)
        edges.append(Edge(owner.id, target.id, Relation.DOCUMENT, Confidence.ESTIMATED, share_text=it.share_text,
                          share_pct=float(pct.group(1)) if pct else None,
                          evidence=[Evidence("document", doc_title, doc_url, locator=it.page, quote=it.quote)],
                          note="문서 추출(LLM) · 사람 검토 필수"))
        review.append(ReviewItem("document_extraction", f"문서 추출: {it.holder_name} → {target.name} ({it.share_text})",
                                 f"{owner.id}->{target.id}", 1, [doc_url] if doc_url else []))
    return ents, edges, review, unknowns
