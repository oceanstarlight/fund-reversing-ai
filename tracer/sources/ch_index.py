"""Local index of Companies House bulk data (no account or API key needed).

Two free, Open Government Licence downloads are loaded into one SQLite file:
- PSC snapshot (daily, JSON lines, ~33 parts): active PSC entries and PSC statements.
- Basic company data (monthly, CSV): company number -> name, for search and display.

Build with ``python scripts/build_ch_index.py``. Ceased PSC entries and unused fields are
dropped while loading, so only what tracing reads is stored.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Iterator

import requests

from tracer import config, names

PSC_PAGE = "https://download.companieshouse.gov.uk/en_pscdata.html"
COMPANY_PAGE = "https://download.companieshouse.gov.uk/en_output.html"
BASE = "https://download.companieshouse.gov.uk/"
BATCH = 50_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS psc (
    company_number TEXT, kind TEXT, name TEXT, natures TEXT, notified_on TEXT,
    reg_no TEXT, country_registered TEXT, place_registered TEXT, country_of_residence TEXT);
CREATE TABLE IF NOT EXISTS statements (company_number TEXT, statement TEXT, notified_on TEXT);
CREATE TABLE IF NOT EXISTS companies (number TEXT PRIMARY KEY, name TEXT, norm TEXT, status TEXT) WITHOUT ROWID;
"""
INDEXES = """
CREATE INDEX IF NOT EXISTS psc_company ON psc(company_number);
CREATE INDEX IF NOT EXISTS statements_company ON statements(company_number);
CREATE INDEX IF NOT EXISTS companies_norm ON companies(norm);
"""


def db_path() -> Path:
    return config.CH_INDEX_PATH


# ------------------------------------------------------------------------- build
def part_links(page_url: str, pattern: str) -> list[str]:
    """Multi-part zip links listed on a download page (the single-file archive is skipped)."""
    html = requests.get(page_url, timeout=config.HTTP_TIMEOUT).text
    links = list(dict.fromkeys(re.findall(r'href="([^"]+\.zip)"', html)))
    return [BASE + h for h in links if re.search(pattern, h)]


def _download(url: str, folder: Path) -> Path:
    path = folder / url.rsplit("/", 1)[-1]
    with requests.get(url, stream=True, timeout=config.HTTP_TIMEOUT) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return path


def _lines(zip_path: Path) -> Iterator[str]:
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            with zf.open(member) as raw:
                yield from io.TextIOWrapper(raw, encoding="utf-8", errors="replace")


def psc_rows(lines) -> Iterator[tuple[str, tuple]]:
    """("psc" | "statements", row) for every active entry in PSC snapshot lines."""
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        d, number = rec.get("data") or {}, rec.get("company_number", "")
        if not number or d.get("ceased_on"):
            continue
        kind = d.get("kind", "")
        if "statement" in kind or "statement" in d:
            yield "statements", (number, d.get("statement", ""), d.get("notified_on", ""))
            continue
        if not kind or kind == "totals#persons-of-significant-control-snapshot":
            continue
        ident = d.get("identification") or {}
        yield "psc", (number, kind, d.get("name", ""), ",".join(d.get("natures_of_control") or []),
                      d.get("notified_on", ""), (ident.get("registration_number") or "").strip(),
                      ident.get("country_registered") or "", ident.get("place_registered") or "",
                      d.get("country_of_residence") or "")


def company_rows(lines) -> Iterator[tuple]:
    reader = csv.reader(lines)
    header = [h.strip() for h in next(reader)]
    i_name, i_num, i_status = header.index("CompanyName"), header.index("CompanyNumber"), header.index("CompanyStatus")
    for row in reader:
        if len(row) > i_status and row[i_num].strip():
            yield (row[i_num].strip().upper(), row[i_name].strip(), names.normalize(row[i_name]), row[i_status].strip())


def _insert(con: sqlite3.Connection, rows: Iterator[tuple[str, tuple]]) -> int:
    buf: dict[str, list[tuple]] = {"psc": [], "statements": [], "companies": []}
    sql = {"psc": "INSERT INTO psc VALUES (?,?,?,?,?,?,?,?,?)", "statements": "INSERT INTO statements VALUES (?,?,?)",
           "companies": "INSERT OR REPLACE INTO companies VALUES (?,?,?,?)"}
    n = 0
    for table, row in rows:
        buf[table].append(row)
        n += 1
        if len(buf[table]) >= BATCH:
            con.executemany(sql[table], buf[table])
            buf[table].clear()
    for table, rows_ in buf.items():
        if rows_:
            con.executemany(sql[table], rows_)
    con.commit()
    return n


def build(companies: bool = True, log=print) -> Path:
    """Download all parts one at a time (each zip is deleted after loading) into a new index."""
    target = db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_db = target.with_suffix(".building")
    tmp_db.unlink(missing_ok=True)
    con = sqlite3.connect(tmp_db)
    con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
    jobs = [("psc", part_links(PSC_PAGE, r"psc-snapshot-.*_\d+of\d+\.zip"))]
    if companies:
        jobs.append(("companies", part_links(COMPANY_PAGE, r"BasicCompanyData-.*-part\d+_\d+\.zip")))
    with tempfile.TemporaryDirectory(dir=target.parent) as folder:
        for job, links in jobs:
            if not links:
                raise RuntimeError(f"no {job} download links found")
            for i, url in enumerate(links, 1):
                zpath = _download(url, Path(folder))
                rows = psc_rows(_lines(zpath)) if job == "psc" else (("companies", r) for r in company_rows(_lines(zpath)))
                n = _insert(con, rows)
                zpath.unlink()
                log(f"{job} {i}/{len(links)}: {n:,} rows · {url.rsplit('/', 1)[-1]}")
            snap = re.search(r"\d{4}-\d{2}-\d{2}", links[0])
            con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"{job}_snapshot", snap.group(0) if snap else ""))
    log("indexing ...")
    con.executescript(INDEXES)
    con.commit()
    con.close()
    tmp_db.replace(target)
    return target


# ------------------------------------------------------------------------- query
class ChIndex:
    def __init__(self, path: Path | None = None):
        self.path = path or db_path()
        self._con: sqlite3.Connection | None = None

    def exists(self) -> bool:
        return self.path.exists()

    def con(self) -> sqlite3.Connection:
        if self._con is None:
            self._con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        return self._con

    def meta(self, key: str) -> str:
        row = self.con().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else ""

    def company(self, number: str) -> tuple[str, str] | None:
        """(name, status) or None."""
        return self.con().execute("SELECT name, status FROM companies WHERE number=?", (number,)).fetchone()

    def search(self, name: str, limit: int = 10) -> list[tuple[str, str, str]]:
        """(number, name, status) whose normalized name equals or starts with the query's first word."""
        q = names.normalize(name)
        if not q:
            return []
        rows = self.con().execute("SELECT number, name, status FROM companies WHERE norm=? LIMIT ?", (q, limit)).fetchall()
        if rows:
            return rows
        first = q.split()[0]
        return self.con().execute(
            "SELECT number, name, status FROM companies WHERE norm >= ? AND norm < ? LIMIT 500",
            (first, first + "￿")).fetchall()

    def psc(self, number: str) -> list[dict]:
        """Active PSC entries in the API's item shape."""
        out = []
        for kind, nm, natures, notified, reg, country, place, residence in self.con().execute(
                "SELECT kind, name, natures, notified_on, reg_no, country_registered, place_registered, "
                "country_of_residence FROM psc WHERE company_number=?", (number,)):
            out.append({"kind": kind, "name": nm, "natures_of_control": [n for n in natures.split(",") if n],
                        "notified_on": notified, "country_of_residence": residence,
                        "identification": {"registration_number": reg, "country_registered": country,
                                           "place_registered": place}})
        return out

    def statements(self, number: str) -> list[dict]:
        return [{"statement": s, "notified_on": d} for s, d in self.con().execute(
            "SELECT statement, notified_on FROM statements WHERE company_number=?", (number,))]
