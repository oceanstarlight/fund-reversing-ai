"""Trace the ownership chain of one entity from the command line.

    python scripts/trace.py "삼성전자"
    python scripts/trace.py 549300G81RQKP7XW2N18 --md reports/out.md --json reports/out.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracer.orchestrator import Tracer  # noqa: E402
from tracer.report import to_dot, to_json, to_markdown  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", help="기업명, LEI, 사업자등록번호, 종목코드, 또는 lei:/dart:/ch:/cik: 접두어 식별자")
    ap.add_argument("--md", help="Markdown 결과 저장 경로")
    ap.add_argument("--json", help="JSON 결과 저장 경로")
    ap.add_argument("--dot", help="Graphviz DOT 저장 경로")
    args = ap.parse_args()

    g = Tracer().trace(args.query)
    md = to_markdown(g)
    print(md)
    for path, text in ((args.md, md), (args.json, to_json(g) if args.json else ""), (args.dot, to_dot(g) if args.dot else "")):
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
