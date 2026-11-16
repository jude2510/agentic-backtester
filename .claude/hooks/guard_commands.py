#!/usr/bin/env python3
"""
PreToolUse guard for shell commands run by coding agents in this repo.

Claude Code runs this before every Bash command, whatever the session's
permission mode, so it holds for crews launched without approval prompts. It
blocks what AGENTS.md's hard rules reserve for the captain: deploys, AWS writes
and invocations, live model calls, merges, force pushes and pushes to main, and
anything touching AWS credentials or an AWS profile other than this project's.

Defence in depth, not a sandbox: a Python script can still reach AWS. The other
layers are no AWS profile in the crew's environment, Claude's auto permission
mode, and branch protection on main.

Reads the hook event as JSON on stdin. Exit 2 blocks the command and shows the
reason to the agent; exit 0 lets it run.
"""

import json
import re
import shlex
import sys

PROJECT_PROFILE = "personal"

# AWS operations that only read. Anything else (create, put, update, delete,
# invoke, start, send, ...) is a change or costs money.
AWS_READ_OPERATION = re.compile(
    r"^(describe|get|list|filter|lookup|search|batch-get|scan|query|head|tail|ls|wait|estimate)\b")
# Reads that hand out credentials are not harmless reads.
AWS_CREDENTIAL_OPERATION = re.compile(r"token|password|secret|credential|login")
AWS_BLOCKED_SERVICES = {"secretsmanager"}
# Read operations whose response includes a secret.
AWS_SECRET_READS = {("cognito-idp", "describe-user-pool-client")}
AWS_GLOBALS_WITH_VALUE = {"--profile", "--region", "--output", "--query", "--endpoint-url",
                          "--cli-read-timeout", "--cli-connect-timeout", "--color", "--ca-bundle"}

CDK_ALLOWED = {"synth", "synthesize", "diff", "ls", "list", "doctor", "docs", "notices",
               "acknowledge", "ack", "flags", "metadata", "version", "context"}
AGENTCORE_ALLOWED = {"status", "logs", "validate", "traces", "help"}

GH_BLOCKED = {("pr", "merge"), ("repo", "delete"), ("repo", "edit"), ("repo", "archive"),
              ("repo", "rename"), ("release", "delete"), ("auth", "token"),
              ("secret", None), ("variable", None)}

PROTECTED_BRANCHES = {"main", "master"}

# Words that only run the command after them, and those wrappers' options that
# take a value (so the value isn't mistaken for the command).
WRAPPERS = {"sudo", "time", "nohup", "exec", "command", "env", "npx", "bunx", "uvx",
            "xargs", "nice", "timeout", "caffeinate"}
WRAPPER_OPTIONS_WITH_VALUE = {"-u", "--unset", "-C", "--chdir", "-I", "-n", "-P", "-L", "-d",
                              "-s", "-g", "--group", "-k", "--kill-after", "--signal", "--package"}
# Package runners whose `run`/`exec` subcommand runs the command after it.
RUNNERS = {"uv", "npm", "pnpm", "yarn", "bun", "poetry", "pipenv"}
RUNNER_SUBCOMMANDS = {"run", "exec", "dlx", "x"}
RUNNER_OPTIONS_WITH_VALUE = {"--with", "--with-requirements", "--python", "--project", "--directory",
                             "--env-file", "--extra", "--group", "--package", "--from", "-w", "-p"}
SHELLS = {"bash", "sh", "zsh", "dash"}
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;", "\n"}
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)


class Blocked(Exception):
    pass


def check_command(command: str) -> None:
    """Raise Blocked if any part of the command is reserved for the captain."""
    for pattern, reason in (
        (r"\.aws/(credentials|config)\b", "reads or changes the AWS credential files"),
        (r"\bAWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)\s*=", "sets AWS keys inline"),
    ):
        if re.search(pattern, command):
            raise Blocked(f"this command {reason}")

    for segment in _segments(_without_heredoc_bodies(command)):
        _check_segment(segment)


def _without_heredoc_bodies(command: str) -> str:
    """Drop heredoc bodies: they are file contents, not commands."""
    lines, out, delimiter = command.split("\n"), [], None
    for line in lines:
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        out.append(line)
        match = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        if match:
            delimiter = match.group(1)
    return "\n".join(out)


def _segments(command: str) -> list:
    """Split a shell command into its simple commands, each a token list."""
    try:
        lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        # Unbalanced quotes: fall back to a plain split so checks still run.
        tokens = re.split(r"\s+|(&&|\|\||;|\||\(|\))", command)
        tokens = [t for t in tokens if t]
    segments, current = [], []
    for token in tokens:
        if token in SEPARATORS or set(token) <= set(";&|()"):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _strip_leading(tokens: list, options_with_value: set) -> list:
    """Drop leading options (and their values) from a token list."""
    tokens = list(tokens)
    while tokens and tokens[0].startswith("-"):
        option = tokens.pop(0)
        if option in options_with_value and "=" not in option and tokens:
            tokens.pop(0)
    return tokens


def _check_segment(tokens: list) -> None:
    tokens = list(tokens)
    while tokens:
        word = tokens[0]
        name = word.rsplit("/", 1)[-1]
        assignment = ASSIGNMENT.match(word)
        if assignment:
            _check_profile_assignment(assignment.group(1), assignment.group(2))
            tokens.pop(0)
        elif name in WRAPPERS:
            tokens = _strip_leading(tokens[1:], WRAPPER_OPTIONS_WITH_VALUE)
            if name == "timeout" and tokens:
                tokens.pop(0)  # the duration
        else:
            break
    if not tokens:
        return

    tool = tokens[0].rsplit("/", 1)[-1]
    args = tokens[1:]
    if tool == "export":
        for word in args:
            assignment = ASSIGNMENT.match(word)
            if assignment:
                _check_profile_assignment(assignment.group(1), assignment.group(2))
        return
    if tool in RUNNERS and args and args[0] in RUNNER_SUBCOMMANDS:
        _check_segment(_strip_leading(args[1:], RUNNER_OPTIONS_WITH_VALUE))
        return
    if tool in SHELLS:
        for i, arg in enumerate(args):
            if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
                if i + 1 < len(args):
                    check_command(args[i + 1])
                return
        return
    if tool == "eval":
        check_command(" ".join(args))
        return

    if tool == "aws":
        _check_aws(args)
    elif tool in ("cdk", "aws-cdk") or tool.startswith("aws-cdk@"):
        _check_cdk(args)
    elif tool == "agentcore":
        _check_agentcore(args)
    elif tool == "git":
        _check_git(args)
    elif tool == "gh":
        _check_gh(args)
    elif tool in ("curl", "wget", "http", "https"):
        _check_http(args)


def _check_profile_assignment(name: str, value: str) -> None:
    if name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE") and value not in ("", PROJECT_PROFILE):
        raise Blocked(f"only the '{PROJECT_PROFILE}' AWS profile may be used in this repo")


def _positional(args: list, options_with_value=frozenset()) -> list:
    out, skip = [], False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg.startswith("-"):
            if "=" not in arg and arg in options_with_value:
                skip = True
            continue
        out.append(arg)
    return out


def _check_aws(args: list) -> None:
    for i, arg in enumerate(args):
        value = None
        if arg == "--profile" and i + 1 < len(args):
            value = args[i + 1]
        elif arg.startswith("--profile="):
            value = arg.split("=", 1)[1]
        if value is not None and value != PROJECT_PROFILE:
            raise Blocked(f"only the '{PROJECT_PROFILE}' AWS profile may be used in this repo")

    words = _positional(args, AWS_GLOBALS_WITH_VALUE)
    if not words or words[0] == "help":
        return
    service = words[0]
    operation = words[1] if len(words) > 1 else ""
    if service in AWS_BLOCKED_SERVICES:
        raise Blocked(f"'aws {service}' handles secrets")
    if service == "configure":
        if operation in ("list", "list-profiles"):
            return
        raise Blocked("'aws configure' can print or change credentials")
    if (service, operation) in AWS_SECRET_READS:
        raise Blocked(f"'aws {service} {operation}' returns a secret")
    if service == "ssm" and "--with-decryption" in args:
        raise Blocked("decrypting SSM parameters exposes secrets")
    if service == "sts" and operation != "get-caller-identity":
        raise Blocked("only 'aws sts get-caller-identity' is allowed")
    if not operation or not AWS_READ_OPERATION.match(operation):
        raise Blocked(f"'aws {service} {operation}' changes AWS or costs money; "
                      "only read-only AWS commands are allowed (the captain runs the rest)")
    if AWS_CREDENTIAL_OPERATION.search(operation):
        raise Blocked(f"'aws {service} {operation}' hands out credentials")


def _check_cdk(args: list) -> None:
    words = _positional(args, {"--app", "-a", "--profile", "--context", "-c", "--output", "-o"})
    command = words[0] if words else ""
    if command and command not in CDK_ALLOWED:
        raise Blocked(f"'cdk {command}' changes AWS; the captain runs deploys "
                      "(say which one the change needs in the PR body)")


def _check_agentcore(args: list) -> None:
    words = _positional(args)
    command = words[0] if words else ""
    if command and command not in AGENTCORE_ALLOWED:
        raise Blocked(f"'agentcore {command}' deploys, invokes or changes the agents; "
                      "the captain runs it")


def _check_git(args: list) -> None:
    words = _positional(args, {"-C", "-c", "--git-dir", "--work-tree"})
    if not words or words[0] != "push":
        return
    push_args = args[args.index("push") + 1:] if "push" in args else []
    if any(a in ("--force", "-f", "--mirror") for a in push_args):
        raise Blocked("force and mirror pushes are not allowed (--force-with-lease is)")
    for arg in _positional(push_args, {"-o", "--push-option", "--repo"}):
        target = arg.split(":")[-1].removeprefix("refs/heads/").lstrip("+")
        if arg.startswith("+"):
            raise Blocked("'+refspec' is a force push")
        if target in PROTECTED_BRANCHES:
            raise Blocked(f"pushing to {target} is not allowed; open a pull request")


def _check_gh(args: list) -> None:
    words = _positional(args, {"-R", "--repo"})
    if not words:
        return
    pair = (words[0], words[1] if len(words) > 1 else None)
    if pair in GH_BLOCKED or (words[0], None) in GH_BLOCKED:
        raise Blocked(f"'gh {' '.join(w for w in pair if w)}' is reserved for the captain")


def _check_http(args: list) -> None:
    joined = " ".join(args)
    live_model_route = re.search(r"amplifyapp\.com/api/(execute-backtest-async|chat)\b", joined)
    sends_data = re.search(r"(^|\s)(-X\s*POST|--request\s*POST|-d|--data\S*|--json|-F|--form)(\s|=|$)", joined)
    if live_model_route and sends_data:
        raise Blocked("posting to the live site's backtest or chat API runs real model calls")


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    command = (event.get("tool_input") or {}).get("command") or ""
    try:
        check_command(command)
    except Blocked as e:
        print(f"Blocked by .claude/hooks/guard_commands.py: {e}. "
              "See the hard rules in AGENTS.md.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
