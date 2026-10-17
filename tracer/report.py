"""Render a traced graph as Graphviz DOT, Markdown (투자심의 첨부용) or JSON. No LLM."""

from __future__ import annotations

import json

from tracer import config
from tracer.models import (
    CONFIDENCE_KO, OWNERSHIP_RELATIONS, RELATION_KO, TERMINAL_KO, Confidence, Edge, Entity, EntityKind,
    OwnershipGraph, Relation,
)

_EDGE_STYLE = {
    Confidence.CONFIRMED: 'color="#1f7a4d" style=solid penwidth=1.6',
    Confidence.ESTIMATED: 'color="#c77700" style=dashed penwidth=1.4',
    Confidence.UNVERIFIABLE: 'color="#8a8a8a" style=dotted penwidth=1.4',
}
_ACCOUNTING_STYLE = 'color="#2f5fb3" style=dashed arrowhead=empty'
_SHAPE = {
    EntityKind.INDIVIDUAL: "ellipse", EntityKind.FOUNDATION: "house", EntityKind.PUBLIC_BODY: "hexagon",
    EntityKind.FUND: "cylinder",
}


def _q(s: str) -> str:
    """Quoted DOT string; real newlines become DOT line breaks."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def display_split(g: OwnershipGraph) -> tuple[list[Edge], dict[str, list[Edge]]]:
    """(edges to draw, folded holder edges per owned entity id).

    Kept per owned entity: corporate holders (the ones traced further), holders without a single
    disclosed number (ranges), and holders at or above DISPLAY_MIN_PCT, at most
    DISPLAY_MAX_HOLDERS by disclosed share. Accounting-parent edges are always drawn."""
    shown: list[Edge] = []
    folded: dict[str, list[Edge]] = {}
    by_owned: dict[str, list[Edge]] = {}
    for ed in g.edges:
        if ed.relation in OWNERSHIP_RELATIONS:
            by_owned.setdefault(ed.owned_id, []).append(ed)
        else:
            shown.append(ed)
    for owned, eds in by_owned.items():
        def important(ed: Edge) -> bool:
            owner = g.entities[ed.owner_id]
            return (owner.kind in (EntityKind.COMPANY, EntityKind.UNKNOWN) or ed.share_pct is None
                    or ed.share_pct >= config.DISPLAY_MIN_PCT)
        keep = sorted((e for e in eds if important(e)), key=lambda e: -(e.share_pct if e.share_pct is not None else 101))
        keep = keep[:config.DISPLAY_MAX_HOLDERS]
        shown += keep
        rest = [e for e in eds if e not in keep]
        if rest:
            folded[owned] = rest
    return shown, folded


def shown_entity_ids(g: OwnershipGraph, shown: list[Edge]) -> set[str]:
    return {g.root_id} | {i for e in shown for i in (e.owner_id, e.owned_id)}


def to_dot(g: OwnershipGraph) -> str:
    """Owners on top, the queried entity at the bottom. Small holders are folded (display_split)."""
    shown, folded = display_split(g)
    ids = shown_entity_ids(g, shown)
    lines = ["digraph G {", "  rankdir=BT; node [fontname=\"Helvetica\" fontsize=11 style=filled fillcolor=\"#ffffff\"];"]
    cyc = {i for f in g.flags for i in f.path}
    for e in g.entities.values():
        if e.id not in ids:
            continue
        label = e.name + (f"\n[{e.jurisdiction}]" if e.jurisdiction else "")
        if e.terminal is not None:
            label += f"\n· {TERMINAL_KO[e.terminal]}"
        fill = "#fff3c4" if e.id == g.root_id else "#fde2e2" if e.id in cyc else "#f2f2f2" if e.terminal else "#ffffff"
        shape = _SHAPE.get(e.kind, "box")
        if e.terminal is not None and e.kind == EntityKind.COMPANY:
            shape = "octagon" if e.terminal.value in ("data_end", "unresolved", "unsupported", "limit") else "box"
        lines.append(f"  {_q(e.id)} [label={_q(label)} shape={shape} fillcolor=\"{fill}\"];")
    for ed in shown:
        accounting = ed.relation in (Relation.ACCOUNTING_PARENT, Relation.ULTIMATE_ACCOUNTING_PARENT)
        style = _ACCOUNTING_STYLE if accounting else _EDGE_STYLE[ed.confidence]
        label = ed.share_text + ("%" if ed.share_pct is not None and not ed.share_text.endswith("%") else "")
        if accounting:
            label = RELATION_KO[ed.relation]
        lines.append(f"  {_q(ed.owned_id)} -> {_q(ed.owner_id)} [{style} label={_q(label)} fontsize=9 dir=back];")
    for owned, eds in folded.items():
        node = f"folded:{owned}"
        lines.append(f"  {_q(node)} [label={_q(f'그 외 보유자 {len(eds)}명' + chr(10) + '(근거 표에서 확인)')} "
                     f"shape=note fillcolor=\"#f7f7f7\" fontcolor=\"#555555\"];")
        lines.append(f"  {_q(owned)} -> {_q(node)} [color=\"#9a9a9a\" style=dashed dir=back];")
    lines.append("}")
    return "\n".join(lines)


def key_terminals(g: OwnershipGraph) -> tuple[list[Entity], int]:
    """(terminals drawn in the graph, number of other terminals folded away)."""
    shown, _ = display_split(g)
    ids = shown_entity_ids(g, shown)
    terms = g.terminals()
    key = [e for e in terms if e.id in ids]
    return key, len(terms) - len(key)


def to_markdown(g: OwnershipGraph) -> str:
    root = g.entities.get(g.root_id)
    out = [f"# 지배구조 추적 결과: {root.name if root else g.query}", "",
           f"- 입력: `{g.query}` · 생성: {g.created_at}", f"- {config.SUPPORTED_SCOPE_KO}", ""]
    if root is None:
        out += ["입력을 하나의 법인으로 확정하지 못했다. 후보:", ""]
        out += [f"- {c['name']} ({c['jurisdiction'] or '-'}) `{c['id']}` 유사도 {c['score']}" for c in g.candidates]
        return "\n".join(out)
    out += ["## 종착점", ""]
    key, others = key_terminals(g)
    for e in key:
        out.append(f"- **{e.name}** — {TERMINAL_KO[e.terminal]}" + (f" ({e.terminal_note})" if e.terminal_note else ""))
    if others:
        out.append(f"- 그 외 소액 보유자 {others}명 (연결과 근거 표 참고)")
    out += ["", "## 연결과 근거", "", "| 보유자 | 대상 | 관계 | 지분(원문) | 확신도 | 기준 시점 | 근거 |", "|---|---|---|---|---|---|---|"]
    for ed in g.edges:
        ev = ed.evidence[0] if ed.evidence else None
        link = f"[{ev.title}]({ev.url})" if ev else "-"
        out.append(f"| {g.entities[ed.owner_id].name} | {g.entities[ed.owned_id].name} | {RELATION_KO[ed.relation]} | "
                   f"{ed.share_text or '-'} | {CONFIDENCE_KO[ed.confidence]} | {ev.as_of if ev else '-'} | {link} |")
    if g.flags:
        out += ["", "## 구조 이상", ""]
        out += [f"- {'상호출자' if f.kind == 'mutual' else '순환출자'}: " + " · ".join(g.entities[i].name for i in f.path) for f in g.flags]
    out += ["", "## 사람 검토 목록", ""]
    out += [f"- [P{r.priority}] {r.message}" + (f" ([원문]({r.links[0]}))" if r.links else "") for r in g.review] or ["- 없음"]
    if g.unknowns:
        out += ["", "## 한계·미확인", ""]
        out += [f"- {u.message}" + (f" — {u.detail}" if u.detail else "") for u in _dedupe(g.unknowns)]
    return "\n".join(out)


def _dedupe(items):
    seen, out = set(), []
    for u in items:
        if (u.code, u.message, u.detail) not in seen:
            seen.add((u.code, u.message, u.detail))
            out.append(u)
    return out


def to_json(g: OwnershipGraph) -> str:
    return json.dumps(g.to_dict(), ensure_ascii=False, indent=2, default=lambda o: getattr(o, "value", str(o)))
