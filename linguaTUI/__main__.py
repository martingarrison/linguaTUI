"""
Entry point for linguaTUI.

One-shot mode (exits after run):
    linguaTUI --prompt "Your task" --model gpt-5.4-mini --harness trae-agent

Interactive mode (keeps running for multi-turn conversation):
    linguaTUI --model gpt-5.4-mini --harness trae-agent

If --model and/or --harness are omitted, a config screen will appear at
startup to let you choose interactively.
"""

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linguaTUI",
        description="A Textual TUI for linguaclaw",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="Task prompt (omit for interactive multi-turn mode).",
    )
    parser.add_argument(
        "--task-file",
        default=None,
        help="Path to a task markdown file.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="LiteLLM model string (e.g. gpt-5.4-mini, claude-sonnet-4-5)."
        " If omitted, a config screen will appear at startup.",
    )
    parser.add_argument(
        "--harness",
        action="append",
        default=[],
        help="Harness to load (may be passed multiple times)."
        " If omitted, a config screen will appear at startup.",
    )
    parser.add_argument(
        "--runtime-policy",
        default=None,
        help="Path to runtime policy SKILL.md.",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace directory.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Maximum number of steps.",
    )
    parser.add_argument(
        "--default-bash-timeout",
        type=int,
        default=None,
        help="Default bash timeout in seconds.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args, extra = parser.parse_known_args()

    # Base args — things that don't change per-run and aren't configurable
    # in the startup screen (yet).
    linguaclaw_args = ["run", "--json"]
    if args.task_file:
        linguaclaw_args += ["--task-file", args.task_file]
    if args.runtime_policy:
        linguaclaw_args += ["--runtime-policy", args.runtime_policy]
    if args.max_steps is not None:
        linguaclaw_args += ["--max-steps", str(args.max_steps)]
    if args.default_bash_timeout is not None:
        linguaclaw_args += ["--default-bash-timeout", str(args.default_bash_timeout)]

    from linguaTUI.app import LinguaTUIApp
    app = LinguaTUIApp(
        linguaclaw_args=linguaclaw_args,
        initial_prompt=args.prompt,
        cli_model=args.model,
        cli_harnesses=args.harness,
        cli_workspace=args.workspace,
    )
    app.run()


if __name__ == "__main__":
    main()
