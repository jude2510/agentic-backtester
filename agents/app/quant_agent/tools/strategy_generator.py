"""
Strategy Generator Tool
Has the strategy generator runtime turn the strategy form into Backtrader code.
"""

import re
from step_types import GeneratedStrategy, StepError
from tools.strategy_sandbox import SandboxViolation, validate_strategy_code
from tools.sub_agents import invoke_sub_agent


def generate_strategy(strategy: dict) -> GeneratedStrategy:
    """Pipeline step: have the strategy generator runtime write Backtrader code.

    The sandbox's static check runs here as well as before execution, so code
    it would reject stops the run at this step, attributed to the generator,
    instead of surfacing later as a backtest failure.
    """
    data = invoke_sub_agent("STRATEGY_GENERATOR_RUNTIME_ARN", strategy, "strategy generator")

    code = str(data.get("code") or "").strip()
    if len(code) < 50:
        raise StepError("the strategy generator returned no usable code")

    try:
        code = validate_strategy_code(code)  # also strips markdown fences
    except SandboxViolation as e:
        raise StepError(f"the generated code was rejected by the safety check: {e}")

    match = re.search(r"^\s*class\s+(\w+)\s*\(", code, re.MULTILINE)
    return GeneratedStrategy(code=code, class_name=match.group(1) if match else None,
                             version=data.get("version", "unknown"))
