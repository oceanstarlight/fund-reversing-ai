"""Build the local Companies House index from the free bulk downloads (no account needed).

    python scripts/build_ch_index.py                # PSC snapshot + company names
    python scripts/build_ch_index.py --psc-only     # skip the monthly company-name file

Parts are downloaded one at a time and deleted after loading. Re-run to refresh
(PSC daily, company names monthly).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracer.sources import ch_index  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--psc-only", action="store_true", help="회사명 데이터 없이 PSC만 적재")
    args = ap.parse_args()
    t0 = time.time()
    path = ch_index.build(companies=not args.psc_only)
    print(f"done: {path} ({path.stat().st_size / 2**30:.2f} GiB, {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
