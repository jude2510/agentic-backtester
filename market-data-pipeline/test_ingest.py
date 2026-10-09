"""
Tests for the ingest's two date rules: the write floor, the one decision that
can delete history, and the end cap that keeps partial bars out.

Needs the pipeline's own venv (ingest imports pyiceberg and httpx at module
level) but no AWS credentials, no API key and no network: both rules are pure.

    cd market-data-pipeline && .venv/bin/python test_ingest.py
"""

import datetime as dt
import sys

from ingest import OVERLAP_DAYS, last_final_day, write_floor

D = dt.date


def utc(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=dt.timezone.utc)


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

    # The run that stored a partial bar: manual, 2026-09-30 10:03 PDT, which is
    # 13:03 in New York with the market open. Today's bar must not be fetched.
    check("a run during market hours stops at yesterday",
          last_final_day(utc(2026, 9, 30, 17, 3)), D(2026, 9, 29))

    # The scheduled run: 02:00 UTC is 22:00 the previous evening in New York
    # (EDT), after extended hours, so that day's bar is final.
    check("the scheduled run includes the day that just closed",
          last_final_day(utc(2026, 10, 1, 2, 0)), D(2026, 9, 30))

    # Winter (EST, UTC-5): 00:30 UTC is 19:30 New York, extended hours still
    # trading; 01:30 UTC is 20:30, done.
    check("winter, before 20:00 New York: stops at the day before",
          last_final_day(utc(2026, 12, 1, 0, 30)), D(2026, 11, 29))
    check("winter, after 20:00 New York: includes that day",
          last_final_day(utc(2026, 12, 1, 1, 30)), D(2026, 11, 30))

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
