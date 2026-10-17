"""Live smoke test against the real sources (needs network; keys from env or .env).

    python scripts/smoke.py            # connectivity checks
    python scripts/smoke.py --trace    # plus a short trace of a GLEIF-only chain

Exit code 1 if GLEIF, SEC or DART fails. Companies House and the LLM are optional.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracer.diagnostics import run_checks  # noqa: E402
from tracer.orchestrator import Tracer  # noqa: E402

REQUIRED = {"GLEIF", "SEC EDGAR", "DART"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trace", action="store_true")
    args = ap.parse_args()
    t = Tracer()
    checks = run_checks(t)
    for c in checks:
        print(f"{'OK  ' if c.ok else 'FAIL'} {c.source}: {c.message}")
    if args.trace:
        g = t.trace("549300G81RQKP7XW2N18")  # Apple Operations International (IE) -> Apple Inc.
        print(f"trace: {len(g.entities)} entities, {len(g.edges)} edges, terminals="
              + ", ".join(f"{e.name}:{e.terminal.value}" for e in g.terminals()))
        if not g.edges:
            sys.exit(1)
    if any(not c.ok for c in checks if c.source in REQUIRED):
        sys.exit(1)


if __name__ == "__main__":
    main()
