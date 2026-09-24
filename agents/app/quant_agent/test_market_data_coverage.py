"""
Tests for the market-data coverage check.

Dependency-free like test_strategy_sandbox.py: `httpx` and `config` are stubbed,
and the tool module is loaded by path, so this runs without the gateway,
credentials or the agent's venv.

    cd agents/app/quant_agent && python3 test_market_data_coverage.py
"""

import datetime as dt
import importlib.util
import pathlib
import sys
import types

sys.modules.setdefault("httpx", types.ModuleType("httpx"))
sys.modules.setdefault("config", types.ModuleType("config"))

_MODULE_PATH = pathlib.Path(__file__).parent / "tools" / "market_data.py"
_spec = importlib.util.spec_from_file_location("market_data", _MODULE_PATH)
_md = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_md)

check_coverage = _md.check_coverage


def bars(start: str, end: str) -> list:
    """Weekday bars from start to end inclusive, in the tool's row shape."""
    d, stop, out = dt.date.fromisoformat(start), dt.date.fromisoformat(end), []
    while d <= stop:
        if d.weekday() < 5:
            out.append({"date": d.isoformat(), "close": 1.0})
        d += dt.timedelta(days=1)
    return out


def main() -> int:
    failures = []

    def check(name, ok, detail=""):
        print(f"{'✅' if ok else '❌'} {name}{'' if ok else ' — ' + detail}")
        if not ok:
            failures.append(name)

    # The live state on 2026-09-23: the table stopped at 2026-08-14 and a 1Y
    # request ends today. Before this check the backtest ran silently on it.
    w = check_coverage(bars("2025-09-23", "2026-08-14"), "2025-09-23", "2026-09-23")
    check("stale data is flagged",
          len(w) == 1 and "ends 2026-08-14" in w[0] and "40 days" in w[0], str(w))

    # A symbol whose history starts after the requested window (5Y of data, 10Y asked).
    w = check_coverage(bars("2021-08-17", "2026-09-22"), "2016-09-23", "2026-09-23")
    check("short history is flagged", len(w) == 1 and "starts 2021-08-17" in w[0], str(w))

    # Normal Monday-morning read: window starts on a Saturday and the last bar
    # is the previous Friday. Weekend gaps must not read as missing data.
    w = check_coverage(bars("2025-09-22", "2026-09-18"), "2025-09-20", "2026-09-21")
    check("weekend boundaries are not flagged", w == [], str(w))

    # Unsorted rows: the check must use min/max, not first/last.
    rows = list(reversed(bars("2025-09-23", "2026-09-22")))
    check("row order does not matter", check_coverage(rows, "2025-09-23", "2026-09-23") == [])

    check("empty result is flagged", check_coverage([], "2025-09-23", "2026-09-23") != [])

    print("\n" + "=" * 56)
    if failures:
        print(f"FAILED — {len(failures)} problem(s)")
        return 1
    print("ALL COVERAGE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
