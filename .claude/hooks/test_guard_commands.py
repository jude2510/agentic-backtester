"""
Tests for the shell-command guard (guard_commands.py).

Commands are ones agents actually run here: what must pass so work isn't
blocked, and what must be stopped, including the wrapped forms (timeout,
bash -c, uv run, env -u, subshells) an agent might reach for. Standard library
only:

    python3 .claude/hooks/test_guard_commands.py
"""

import importlib.util
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
_spec = importlib.util.spec_from_file_location("guard_commands", HERE / "guard_commands.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

LIVE = "https://main.d1x6utcih65eg5.amplifyapp.com"

ALLOWED = [
    "cd agents/app/quant_agent && uv run python test_pipeline.py",
    "export AWS_PROFILE=personal AWS_REGION=us-east-1; aws logs filter-log-events --log-group-name g --start-time 1",
    "AWS_PROFILE=personal aws lambda get-function --function-name f --query 'Code.ImageUri' --output text",
    "aws sts get-caller-identity --query Account --output text",
    "aws s3 ls",
    "aws dynamodb scan --table-name agentic-backtest-jobs",
    "aws ce get-cost-and-usage --time-period Start=2026-09-01,End=2026-10-01 --granularity MONTHLY --metrics UnblendedCost",
    "aws configure list --profile personal",
    "aws --profile personal ecr describe-images --repository-name r",
    "aws logs tail /aws/lambda/agentic-backtest-ingest --since 10m",
    "cdk synth --quiet",
    "cd infra && source .venv/bin/activate && cdk diff agentic-backtest-hosting",
    "npx cdk ls",
    "agentcore status",
    "git push -u origin fm/news-step",
    "git push --force-with-lease origin fm/news-step",
    "gh pr create --title 'feat: news' --body 'why'",
    "gh pr view 12 --json state",
    "gh auth status",
    "grep -rn 'cdk deploy' README.md DEPLOYMENT_GUIDE.md",
    "echo 'aws lambda invoke is reserved for the captain'",
    "cat > notes.md <<'EOF'\ncdk deploy agentic-backtest-hosting\naws lambda invoke --function-name w out\nEOF",
    f"curl -s {LIVE}/api/health",
    f"curl -s '{LIVE}/api/execute-backtest-async?jobId=abc'",
    "npm run build",
    "timeout 30 npm test",
    "python3 -c \"print('aws lambda invoke')\"",
    "git status --short && git diff --stat",
]

BLOCKED = [
    "cdk deploy agentic-backtest-hosting",
    "cd infra && source .venv/bin/activate && cdk deploy agentic-backtest-backend agentic-backtest-pipeline",
    "npx aws-cdk@latest destroy --all",
    "(cd agents && agentcore deploy -y)",
    "agentcore invoke '{\"prompt\": \"hi\"}'",
    "aws lambda invoke --function-name agentic-backtest-worker out.json",
    "aws cloudformation delete-stack --stack-name agentic-backtest-hosting",
    "aws ecr batch-delete-image --repository-name r --image-ids imageTag=x",
    "aws secretsmanager get-secret-value --secret-id agentic-backtest/massive-api-key",
    "aws ssm get-parameter --name /x --with-decryption",
    "aws cognito-idp describe-user-pool-client --user-pool-id p --client-id c --query UserPoolClient.ClientSecret",
    "aws configure get aws_secret_access_key --profile personal",
    "aws ecr get-login-password",
    "aws sts assume-role --role-arn arn:aws:iam::1:role/r --role-session-name s",
    "aws --profile another-account s3 ls",
    "AWS_PROFILE=another-account aws s3 ls",
    "export AWS_PROFILE=another-account",
    "cat ~/.aws/credentials",
    "AWS_SECRET_ACCESS_KEY=abc aws s3 ls",
    "git push origin main",
    "git push origin HEAD:main",
    "git push --force origin fm/news-step",
    "git push origin +fm/news-step",
    "git -C /tmp/repo push -f",
    "gh pr merge 12 --squash",
    "gh repo delete jude2510/agentic-backtester --yes",
    "gh auth token",
    "gh secret set MASSIVE_API_KEY",
    f"curl -X POST {LIVE}/api/execute-backtest-async -d '{{}}'",
    f"curl -s {LIVE}/api/chat --json '{{\"prompt\": \"hi\"}}'",
    "bash -c \"aws lambda invoke --function-name w out\"",
    "timeout 60 aws lambda invoke --function-name w out",
    "env -u AWS_PROFILE aws lambda invoke --function-name w out",
    "uv run --with boto3 aws lambda invoke --function-name w out",
    "eval \"cdk deploy agentic-backtest-hosting\"",
    "sudo -E cdk deploy",
    "echo $(aws lambda invoke --function-name w out)",
    "ls; agentcore deploy -y",
    "git status\nagentcore deploy -y",
    "xargs -I{} aws ecr batch-delete-image --repository-name r --image-ids imageTag={}",
]


def blocked(command: str):
    try:
        guard.check_command(command)
        return None
    except guard.Blocked as e:
        return str(e)


def run_hook(event: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HERE / "guard_commands.py")],
                          input=json.dumps(event), capture_output=True, text=True)


def main() -> int:
    failures = []

    def check(name, ok, detail=""):
        if not ok:
            failures.append(name)
            print(f"❌ {name}{' — ' + str(detail) if detail else ''}")

    for command in ALLOWED:
        reason = blocked(command)
        check(f"allowed: {command[:70]!r}", reason is None, reason)
    for command in BLOCKED:
        check(f"blocked: {command[:70]!r}", blocked(command) is not None, "was allowed")
    print(f"{'✅' if not failures else '❌'} {len(ALLOWED)} allowed and {len(BLOCKED)} blocked commands checked")

    # The hook as Claude Code runs it: JSON on stdin, exit 2 + stderr to block.
    stopped = run_hook({"tool_name": "Bash", "tool_input": {"command": "cdk deploy agentic-backtest-hosting"}})
    check("hook process: a blocked command exits 2 with the reason on stderr",
          stopped.returncode == 2 and "AGENTS.md" in stopped.stderr, (stopped.returncode, stopped.stderr))
    passed = run_hook({"tool_name": "Bash", "tool_input": {"command": "npm run build"}})
    check("hook process: an allowed command exits 0 silently",
          passed.returncode == 0 and not passed.stderr, (passed.returncode, passed.stderr))
    other_tool = run_hook({"tool_name": "Write", "tool_input": {"file_path": "x", "content": "cdk deploy"}})
    check("hook process: other tools pass through", other_tool.returncode == 0)
    if not failures:
        print("✅ hook process: exit codes and messages as Claude Code expects")

    print("\n" + "=" * 56)
    if failures:
        print(f"FAILED — {len(failures)} problem(s)")
        return 1
    print("ALL GUARD TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
