"""Offline fake HTTP and source payload builders used by the tests."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path

from tracer.http import Response, public_url


class FakeHttp:
    """Routes: (url, params subset, status, payload). First match wins; unmatched -> 404."""

    def __init__(self):
        self.routes: list[tuple[str, dict, int, object]] = []
        self.calls: list[tuple[str, dict]] = []

    def add(self, url: str, payload, params: dict | None = None, status: int = 200) -> "FakeHttp":
        self.routes.append((url, params or {}, status, payload))
        return self

    def get(self, url, params=None, headers=None, auth=None, ttl_hours=None) -> Response:
        params = params or {}
        self.calls.append((url, params))
        for r_url, r_params, status, payload in self.routes:
            if r_url == url and all(str(params.get(k)) == str(v) for k, v in r_params.items()):
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                return Response(status, body, public_url(url, params))
        return Response(404, b"{}", public_url(url, params))


DART = "https://opendart.fss.or.kr/api"
GLEIF = "https://api.gleif.org/api/v1"


def corpcode_zip(corps: list[dict]) -> bytes:
    items = "".join(
        "<list>" + "".join(f"<{k}>{v}</{k}>" for k, v in c.items()) + "</list>" for c in corps
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", f"<?xml version='1.0' encoding='UTF-8'?><result>{items}</result>")
    return buf.getvalue()


def hyslr(rows: list[tuple[str, str, str]], rcept_no: str = "20261114000001") -> dict:
    """rows: (name, relation, percent)."""
    return {"status": "000", "message": "정상", "list": [
        {"rcept_no": rcept_no, "nm": n, "relate": rel, "stock_knd": "보통주",
         "trmend_posesn_stock_qota_rt": pct, "stlm_dt": "2026-09-30"} for n, rel, pct in rows
    ]}


def lei_record(lei: str, name: str, juris: str, ra: str = "RA000000", reg_as: str = "") -> dict:
    return {"type": "lei-records", "id": lei, "attributes": {
        "lei": lei,
        "entity": {"legalName": {"name": name}, "otherNames": [], "jurisdiction": juris,
                   "legalAddress": {"country": juris[:2]}, "registeredAt": {"id": ra}, "registeredAs": reg_as},
        "registration": {"lastUpdateDate": "2026-05-01T00:00:00Z"},
    }}


def relationship(child: str, parent: str, level: str = "DIRECTLY", corroborated: bool = True) -> dict:
    return {"data": {"type": "relationship-records", "id": f"{child}|{parent}", "attributes": {
        "relationship": {"startNode": {"id": child}, "endNode": {"id": parent},
                         "type": f"IS_{level}_CONSOLIDATED_BY",
                         "periods": [{"type": "ACCOUNTING_PERIOD", "startDate": "2025-01-01", "endDate": "2025-12-31T00:00:00Z"}]},
        "registration": {"corroborationLevel": "FULLY_CORROBORATED" if corroborated else "ENTITY_SUPPLIED_ONLY",
                         "lastUpdateDate": "2026-03-01T00:00:00Z"},
    }}}


def exception(lei: str, reason: str) -> dict:
    return {"data": {"type": "reporting-exceptions", "attributes": {
        "lei": lei, "category": "DIRECT_ACCOUNTING_CONSOLIDATION_PARENT", "reason": reason}}}


def ch_index(path: Path, companies: list[tuple[str, str]], psc: list[dict]) -> Path:
    """Small Companies House index. companies: (number, name); psc: snapshot records
    {"company_number", "data"} as in the bulk file."""
    from tracer.sources import ch_index as chi
    from tracer import names

    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(chi.SCHEMA + chi.INDEXES)
    chi._insert(con, (("companies", (n, nm, names.normalize(nm), "Active")) for n, nm in companies))
    chi._insert(con, chi.psc_rows(json.dumps(r) for r in psc))
    con.execute("INSERT INTO meta VALUES ('psc_snapshot', '2026-10-16')")
    con.commit()
    con.close()
    return path
