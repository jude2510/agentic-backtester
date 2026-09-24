"""
Tests for the ingest write-floor rule — the one decision that can delete history.

Needs the pipeline's own venv (ingest imports pyiceberg and httpx at module
level) but no AWS credentials, no API key and no network: `write_floor` is pure.

    cd market-data-pipeline && .venv/bin/python test_ingest.py
"""

import datetime as dt
import sys

from ingest import OVERLAP_DAYS, write_floor

D = dt.date


def main() -> int:
    failures = []

    def check(name, got, want):
        if got == want:
            print(f"✅ {name}")
        else:
            failures.append(f"{name}: got {got}, want {want}")
            print(f"❌ {name}: got {got}, want {want}")

    # The case that would have wiped the table on 2026-09-23: stored data ends
    # 2026-08-14, and --delta requests only 2026-09-13 onward. The old rule saw
    # no overlap with the requested window and fell through to a full replace.
    stored_through = D(2026, 8, 14)
    check("gap longer than the delta window resumes from the stored end, not a full replace",
          write_floor(stored_through),
          stored_through - dt.timedelta(days=OVERLAP_DAYS))

    # The ordinary daily case: the floor sits OVERLAP_DAYS before the last bar,
    # so recently restated bars are re-fetched and everything older is kept.
    check("daily run re-fetches the overlap only",
          write_floor(D(2026, 9, 22)),
          D(2026, 9, 22) - dt.timedelta(days=OVERLAP_DAYS))

    # Nothing stored means nothing to lose, so a full-symbol write is correct.
    check("new symbol gets a full-symbol write", write_floor(None), None)

    print("\n" + "=" * 56)
    if failures:
        print(f"FAILED — {len(failures)} problem(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL INGEST TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
