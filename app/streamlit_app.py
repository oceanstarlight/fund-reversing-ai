"""Streamlit UI: input -> ownership graph, evidence table, human review list.

    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Streamlit Community Cloud keeps keys in st.secrets; copy top-level string values into the
# environment so tracer.config reads them like a local .env. Existing env vars win.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v)
except Exception:  # noqa: BLE001 - no secrets file locally is normal
    pass

from tracer import config  # noqa: E402
from tracer.diagnostics import run_checks  # noqa: E402
from tracer.events.root_cause import trace_event  # noqa: E402
from tracer.models import CONFIDENCE_KO, RELATION_KO, TERMINAL_KO  # noqa: E402
from tracer.orchestrator import Tracer  # noqa: E402
from tracer.report import key_terminals, to_dot, to_json, to_markdown  # noqa: E402

st.set_page_config(page_title="지배구조 역추적", layout="wide")

# Demo cases from PLANS.md (README §9).
DEMOS = {
    "A 카카오": "카카오",
    "B 한국쉘석유": "한국쉘석유",
    "B S-OIL": "에쓰-오일",
    "C 고려아연": "고려아연",
    "GLEIF·SEC 예시": "549300G81RQKP7XW2N18",
}


@st.cache_resource
def tracer() -> Tracer:
    return Tracer()


def source_status() -> list[str]:
    t = tracer()
    out = []
    for src in (t.dart, t.gleif, t.sec, t.ch):
        ok, reason = src.available()
        out.append(f"{'🟢' if ok else '⚪'} {src.label_ko}" + ("" if ok else f" — {reason}"))
    return out


def show_graph(g) -> None:
    if not g.root_id:
        st.warning("입력을 하나의 법인으로 확정하지 못했어. 후보 중 하나를 골라 다시 추적해줘.")
        for c in g.candidates:
            if st.button(f"{c['name']} ({c['jurisdiction'] or '-'}) · 유사도 {c['score']}", key=c["id"]):
                st.session_state["query"] = c["id"]
                st.session_state["graph"] = tracer().trace(c["id"])
                st.rerun()
        return
    root = g.entities[g.root_id]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("법인·개인", len(g.entities))
    c2.metric("연결", len(g.edges))
    c3.metric("검토 필요", len(g.review))
    c4.metric("구조 이상", len(g.flags))
    st.subheader(f"소유 그래프 · {root.name}")
    st.caption("실선 초록=확인됨 · 주황 점선=추정 · 회색 점선=확인 불가 · 파란 점선=회계상 연결 모회사(GLEIF)")
    st.graphviz_chart(to_dot(g), use_container_width=True)

    tab_edges, tab_review, tab_end, tab_log = st.tabs(["연결과 근거", "사람 검토 목록", "종착점·한계", "추적 로그"])
    with tab_edges:
        rows = []
        # Largest disclosed holdings first; ranges and accounting links (no single number) on top.
        for e in sorted(g.edges, key=lambda e: -(e.share_pct if e.share_pct is not None else 101)):
            ev = e.evidence[0] if e.evidence else None
            rows.append({
                "보유자": g.entities[e.owner_id].name, "대상": g.entities[e.owned_id].name,
                "관계": RELATION_KO[e.relation], "지분(원문)": e.share_text or "-",
                "확신도": CONFIDENCE_KO[e.confidence], "기준 시점": ev.as_of if ev else "",
                "근거": ev.url if ev else "", "원문 인용": ev.quote if ev else "", "비고": e.note,
            })
        st.dataframe(rows, use_container_width=True, hide_index=True,
                     column_config={"근거": st.column_config.LinkColumn("근거", display_text="열기")})
    with tab_review:
        if not g.review:
            st.write("검토할 항목 없음")
        for r in g.review:
            link = f" · [원문]({r.links[0]})" if r.links else ""
            st.markdown(f"**P{r.priority}** {r.message}{link}")
    with tab_end:
        key, others = key_terminals(g)
        for e in key:
            st.markdown(f"- **{e.name}** — {TERMINAL_KO[e.terminal]}" + (f" ({e.terminal_note})" if e.terminal_note else ""))
        if others:
            st.markdown(f"- 그 외 소액 보유자 {others}명 (연결과 근거 표 참고)")
        if g.flags:
            st.error("구조 이상: " + " / ".join(
                ("상호출자 " if f.kind == "mutual" else "순환출자 ") + " · ".join(g.entities[i].name for i in f.path) for f in g.flags))
        seen = set()
        for u in g.unknowns:
            if (u.message, u.detail) not in seen:
                seen.add((u.message, u.detail))
                st.markdown(f"- {u.message}" + (f" — `{u.detail}`" if u.detail else ""))
    with tab_log:
        st.dataframe([{"법인": g.entities[s.entity_id].name if s.entity_id in g.entities else s.entity_id,
                       "소스": s.source, "결과": s.outcome} for s in g.steps], use_container_width=True, hide_index=True)
    d1, d2 = st.columns(2)
    d1.download_button("Markdown 내려받기", to_markdown(g), file_name="ownership.md")
    d2.download_button("JSON 내려받기", to_json(g), file_name="ownership.json")


st.title("지배구조 역추적 에이전트")
st.info(config.SUPPORTED_SCOPE_KO)
with st.sidebar:
    st.header("소스 상태")
    for line in source_status():
        st.write(line)
    st.caption("로컬은 .env, Streamlit Cloud는 앱 설정의 Secrets에 키를 넣는다. 없는 소스는 건너뛰고 '확인 불가'로 남긴다.")
    if st.button("연결 점검"):
        with st.spinner("소스마다 한 번씩 호출 중..."):
            for c in run_checks(tracer()):
                st.write(f"{'✅' if c.ok else '❌'} **{c.source}** — {c.message}")

mode = st.radio("모드", ["지배구조 역추적", "이벤트 원인 추적"], horizontal=True)
if mode == "지배구조 역추적":
    cols = st.columns(len(DEMOS))
    for col, (label, q) in zip(cols, DEMOS.items()):
        if col.button(label, use_container_width=True):
            st.session_state["query"] = q
            with st.spinner("소스를 호출하며 추적 중..."):
                st.session_state["graph"] = tracer().trace(q)
    with st.form("trace"):
        q = st.text_input("기업명 · LEI · 사업자등록번호 · 종목코드 (또는 lei:/dart:/ch:/cik: 식별자)",
                          value=st.session_state.get("query", ""))
        if st.form_submit_button("추적") and q.strip():
            st.session_state["query"] = q.strip()
            with st.spinner("소스를 호출하며 추적 중..."):
                st.session_state["graph"] = tracer().trace(q.strip())
    if "graph" in st.session_state:
        show_graph(st.session_state["graph"])
else:
    with st.form("event"):
        corp = st.text_input("DART 고유번호(8자리)")
        ev_date = st.date_input("이벤트 일자", value=date.today())
        go = st.form_submit_button("원인 후보 찾기")
    if go and corp.strip():
        with st.spinner("공시를 모으는 중..."):
            res = trace_event(tracer(), corp.strip(), ev_date)
        st.caption("원인을 단정하지 않는다. 후보마다 공시 원문을 확인해줘.")
        st.dataframe([{"분류": c.label_ko, "공시": c.title, "접수일": c.filed, "제출인": c.filer, "원문": c.url,
                       "순위 점수": c.score} for c in res.candidates], use_container_width=True, hide_index=True,
                     column_config={"원문": st.column_config.LinkColumn("원문", display_text="열기")})
        for u in res.unknowns:
            st.write(f"- {u.message}")
        if res.graph is not None:
            st.success("소유 구조 관련 공시가 있어 지배구조를 다시 추적했어.")
            show_graph(res.graph)
