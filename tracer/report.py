"""Render a traced graph as Graphviz DOT, Markdown (투자심의 첨부용) or JSON. No LLM."""

from __future__ import annotations

import json

from tracer import config
from tracer.models import (
    CONFIDENCE_KO, RELATION_KO, TERMINAL_KO, Confidence, EntityKind, OwnershipGraph, Relation,
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


def to_dot(g: OwnershipGraph) -> str:
    """Owners on top, the queried entity at the bottom."""
    lines = ["digraph G {", "  rankdir=BT; node [fontname=\"Helvetica\" fontsize=11 style=filled fillcolor=\"#ffffff\"];"]
    cyc = {i for f in g.flags for i in f.path}
    for e in g.entities.values():
        label = e.name + (f"\n[{e.jurisdiction}]" if e.jurisdiction else "")
        if e.terminal is not None:
            label += f"\n· {TERMINAL_KO[e.terminal]}"
        fill = "#fff3c4" if e.id == g.root_id else "#fde2e2" if e.id in cyc else "#f2f2f2" if e.terminal else "#ffffff"
        shape = _SHAPE.get(e.kind, "box")
        if e.terminal is not None and e.kind == EntityKind.COMPANY:
            shape = "octagon" if e.terminal.value in ("data_end", "unresolved", "unsupported", "limit") else "box"
        lines.append(f"  {_q(e.id)} [label={_q(label)} shape={shape} fillcolor=\"{fill}\"];")
    for ed in g.edges:
        accounting = ed.relation in (Relation.ACCOUNTING_PARENT, Relation.ULTIMATE_ACCOUNTING_PARENT)
        style = _ACCOUNTING_STYLE if accounting else _EDGE_STYLE[ed.confidence]
        label = ed.share_text + ("%" if ed.share_pct is not None and not ed.share_text.endswith("%") else "")
        if accounting:
            label = RELATION_KO[ed.relation]
        lines.append(f"  {_q(ed.owned_id)} -> {_q(ed.owner_id)} [{style} label={_q(label)} fontsize=9 dir=back];")
    lines.append("}")
    return "\n".join(lines)


def to_markdown(g: OwnershipGraph) -> str:
    root = g.entities.get(g.root_id)
    out = [f"# 지배구조 추적 결과: {root.name if root else g.query}", "",
           f"- 입력: `{g.query}` · 생성: {g.created_at}", f"- {config.SUPPORTED_SCOPE_KO}", ""]
    if root is None:
        out += ["입력을 하나의 법인으로 확정하지 못했다. 후보:", ""]
        out += [f"- {c['name']} ({c['jurisdiction'] or '-'}) `{c['id']}` 유사도 {c['score']}" for c in g.candidates]
        return "\n".join(out)
    out += ["## 종착점", ""]
    for e in g.terminals():
        out.append(f"- **{e.name}** — {TERMINAL_KO[e.terminal]}" + (f" ({e.terminal_note})" if e.terminal_note else ""))
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
