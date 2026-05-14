"""
Main Textual App for linguaTUI.

Supports two modes:
  - One-shot mode: takes --prompt on the CLI, runs once, exits.
  - Interactive mode: shows an Input widget for multi-turn conversation.
    Each turn is a fresh linguaclaw subprocess; the TUI manages conversation
    history and feeds it forward as compact context.

If --model and/or --harness are missing from the CLI, a ConfigScreen is
shown at startup to let the user pick interactively.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
import litellm

from textual.app import App, ComposeResult
from textual.message import Message
from textual.worker import get_current_worker
from textual.widgets import Header, Footer, Static, Input

from linguaTUI.widgets.conversation import ConversationPane, StreamingPane
from linguaTUI.widgets.details import DetailsSidebar

# Lazy import — ConfigScreen pulls in importlib + pathlib which we
# only need when showing the startup screen.
_config_screen = None


def _get_config_screen():
    global _config_screen
    if _config_screen is None:
        from linguaTUI.screens.config_screen import (
            ConfigScreen,
            find_harnesses,
            read_model_default,
        )
        _config_screen = (ConfigScreen, find_harnesses, read_model_default)
    return _config_screen


class LinguaclawEvent(Message):
    """Message posted from the linguaclaw worker to the TUI."""

    def __init__(self, event_type: str, data: dict) -> None:
        self.event_type = event_type
        self.data = data
        super().__init__()


class LinguaTUIApp(App):
    """A Textual TUI that wraps a linguaclaw subprocess."""

    CSS_PATH = "assets/linguaTUI.tcss"

    TITLE = "linguaTUI"
    SUB_TITLE = "linguaclaw TUI"

    BINDINGS = [
        ("ctrl+c", "interrupt", "Interrupt"),
        ("ctrl+q", "quit", "Quit"),
        ("q", "quit", "Quit"),
        ("ctrl+e", "show_stderr", "Show stderr"),
    ]

    def __init__(
        self,
        linguaclaw_args: list[str],
        initial_prompt: str | None = None,
        cli_model: str | None = None,
        cli_harnesses: list[str] | None = None,
        cli_workspace: str = ".",
    ) -> None:
        """Args:
            linguaclaw_args:  base args passed to every linguaclaw run
                               (things like --json, --task-file, --max-steps, etc.)
            initial_prompt:   if given, run one-shot and exit.
                              if None, enter interactive mode.
            cli_model:        model string from CLI (None = ask at startup)
            cli_harnesses:    harness names from CLI (empty = ask at startup)
            cli_workspace:    workspace directory from CLI
        """
        self._base_args = linguaclaw_args
        self._initial_prompt = initial_prompt

        # Configuration — may be overridden by ConfigScreen
        self._model: str = cli_model or ""
        self._harnesses: list[str] = cli_harnesses or []
        self._workspace: str = cli_workspace

        self._subprocess: asyncio.subprocess.Process | None = None
        self._is_running = False
        self._last_assistant_summary: str = ""
        self.stderr_output: str = ""
        # Accumulated conversation turns, each a dict with "role" and "content"
        self.conversation_history: list[dict[str, str]] = []
        # Session-wide accumulators for cost tracking
        self._session_prompt_tokens: int = 0
        self._session_completion_tokens: int = 0
        self._session_cost: float = 0.0
        super().__init__()

    # ── Layout ──────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Static(id="main-container"):
            with Static(id="conversation-pane-container"):
                yield ConversationPane(id="conversation-pane")
                yield StreamingPane(id="streaming-pane")
            with Static(id="details-pane-container"):
                yield DetailsSidebar(id="details-sidebar")
        yield Input(
            id="prompt-input",
            placeholder="Type your message and press Enter...",
        )
        yield Footer()

    def on_mount(self) -> None:
        # Hide the input until we know whether config is needed
        self.query_one("#prompt-input", Input).display = False

        if self._model and self._harnesses:
            # Fully configured from CLI — jump straight in
            self._start_conversation()
        else:
            # Show config screen
            ConfigScreen, find_harnesses, read_model_default = _get_config_screen()
            groups = find_harnesses()
            default_model = read_model_default() or self._model
            self.push_screen(
                ConfigScreen(
                    default_model=default_model,
                    harnesses=groups,
                    cli_harnesses=self._harnesses,
                    cli_workspace=self._workspace,
                ),
                callback=self._on_config_submitted,
            )

    def _on_config_submitted(self, config: dict | None) -> None:
        """Callback from ConfigScreen.dismiss()."""
        if config is None:
            self.exit()
            return

        self._model = config.get("model", self._model)
        self._harnesses = config.get("harnesses", self._harnesses)
        self._workspace = config.get("workspace", self._workspace)

        self._start_conversation()

    def _start_conversation(self) -> None:
        """Begin the conversation — either one-shot or interactive."""
        # Force unbuffered stdout for subprocess event streaming
        os.environ["PYTHONUNBUFFERED"] = "1"

        input_widget = self.query_one("#prompt-input", Input)

        if self._initial_prompt:
            # One-shot mode — keep input hidden, fire immediately
            self._start_run(self._initial_prompt)
        else:
            # Interactive mode — show input and focus it
            input_widget.display = True
            input_widget.focus()

    # ── Interactive input ───────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user pressing Enter in the input bar."""
        prompt = event.value.strip()
        if not prompt:
            return

        if self._is_running:
            return

        input_widget = self.query_one("#prompt-input", Input)
        input_widget.clear()
        input_widget.disabled = True

        self._start_run(prompt)

    # ── Run lifecycle ───────────────────────────────────────────────

    def _start_run(self, user_prompt: str) -> None:
        """Build the full prompt (with history) and spawn linguaclaw."""
        full_prompt = self._build_full_prompt(user_prompt)

        # Add this turn's user prompt to history before the run
        self.conversation_history.append({"role": "user", "content": user_prompt})

        # Show user's message in the conversation pane
        # (we skip rendering user messages from the event stream)
        conversation = self.query_one("#conversation-pane", ConversationPane)
        conversation.add_user_message(user_prompt)

        self._is_running = True
        self._last_assistant_summary = ""

        # Update status in sidebar
        details = self.query_one("#details-sidebar", DetailsSidebar)
        details.set_status("starting")

        self.run_worker(
            self._run_linguaclaw(full_prompt),
            name="linguaclaw-runner",
            group="linguaclaw",
            exit_on_error=False,
        )

    MAX_HISTORY_CHARS = 30000
    """Maximum total characters for the conversation history portion of the prompt.
    ~30K chars is roughly 7.5K tokens. Combined with background summarization of
    older turns, this keeps recent detail while preserving context from earlier
    turns in condensed form."""

    MIN_KEEP_TURNS = 4
    """Minimum number of most recent (user + assistant) turn pairs to always
    preserve in full when history exceeds MAX_HISTORY_CHARS. Older turns are
    condensed into a background summary.
    """

    def _build_background_summary(self, dropped_turns: list[dict]) -> str:
        """Build a condensed background section from assistant summaries of dropped turns.

        Each assistant turn's ``content`` is already a compact summary of what that
        run produced. When we can't fit all turns in the prompt, we keep those
        summaries as a "conversation background" rather than silently discarding them.
        """
        # Collect assistant summaries from the dropped turn objects
        summaries: list[str] = []
        for turn in dropped_turns:
            if turn["role"] == "assistant" and turn["content"]:
                summaries.append(turn["content"])

        if not summaries:
            return ""

        # Compact each summary and build the background section
        max_per_summary = 400  # characters per condensed entry
        background_lines: list[str] = [
            "## Previous conversation summary",
            "(Condensed from earlier turns that are no longer shown in full.)",
            "",
        ]
        for s in summaries:
            preview = s[:max_per_summary]
            if len(s) > max_per_summary:
                preview += "..."
            background_lines.append(f"* {preview}")

        return "\n".join(background_lines)

    def _build_full_prompt(self, current_prompt: str) -> str:
        """Build the --prompt string that includes conversation history.

        Keeps the most recent MIN_KEEP_TURNS turns in full detail. Older turns
        are condensed into a background summary via _build_background_summary,
        preserving the assistant's own condensed record of what happened.
        """
        # All prior turns (current turn not yet appended to history)
        prior_turns = list(self.conversation_history)
        if not prior_turns:
            return current_prompt

        # Label each turn
        turn_texts: list[str] = []
        for turn in prior_turns:
            label = "You" if turn["role"] == "user" else "Assistant"
            turn_texts.append(f"{label}: {turn['content']}")

        # Truncate if history is too long
        total_history_len = sum(len(t) for t in turn_texts)
        background_text = ""
        condensed_count = 0

        if total_history_len > self.MAX_HISTORY_CHARS:
            # Keep the most recent MIN_KEEP_TURNS turns in full
            preserved = turn_texts[-self.MIN_KEEP_TURNS:]
            # The rest get condensed into a background summary
            dropped_indices = len(turn_texts) - self.MIN_KEEP_TURNS
            condensed_count = dropped_indices

            if dropped_indices > 0:
                # Get the original turn objects for the dropped portion
                dropped_turns = prior_turns[:dropped_indices]
                background_text = self._build_background_summary(dropped_turns)

            # Check if preserved turns still exceed budget
            preserved_len = sum(len(t) for t in preserved)
            if preserved_len > self.MAX_HISTORY_CHARS:
                # Even the minimum turns exceed budget -- truncate individual entries
                budget_per_turn = self.MAX_HISTORY_CHARS // len(preserved)
                trimmed: list[str] = []
                for t in preserved:
                    if len(t) > budget_per_turn:
                        trimmed.append(t[:budget_per_turn] + "...[truncated]")
                    else:
                        trimmed.append(t)
                preserved = trimmed

            turn_texts = preserved

        # Build the prompt
        parts: list[str] = [
            "The user is having a continuing conversation with you. "
            "Previous turns are shown below.",
            "",
        ]

        # Insert background summary (if any old turns were condensed)
        if background_text:
            parts.append(background_text)
            parts.append("")

        parts.append("## Recent conversation")

        # Insert condensation note
        if condensed_count > 0:
            note = (
                f"[{condensed_count} earlier turn(s) condensed into background summary. "
                f"Showing the last {len(turn_texts)} turn(s) in full.]"
            )
            parts.append(note)

        parts.extend(turn_texts)

        parts.append("")
        parts.append("## Current instruction")
        parts.append(current_prompt)
        return "\n".join(parts)

    def _on_run_finished(self) -> None:
        """Called after a run completes or fails — re-enable input (interactive)
        or exit (one-shot)."""
        self._is_running = False

        if self._initial_prompt:
            self.exit()
            return

        # Interactive mode — re-enable input
        input_widget = self.query_one("#prompt-input", Input)
        input_widget.disabled = False
        input_widget.focus()

    # ── Subprocess ──────────────────────────────────────────────────

    def _build_run_args(self) -> list[str]:
        """Build the full linguaclaw args including model, harnesses, and workspace."""
        args = list(self._base_args)

        if self._model:
            args += ["--model", self._model]
        for h in self._harnesses:
            args += ["--harness", h]
        if self._workspace:
            args += ["--workspace", self._workspace]

        return args

    async def _run_linguaclaw(self, prompt: str) -> None:
        """Spawn linguaclaw as a subprocess and stream JSONL events."""
        worker = get_current_worker()

        args = self._build_run_args() + ["--prompt", prompt]

        try:
            # Inherit parent env but force unbuffered stdout for real-time event streaming
            subprocess_env = os.environ.copy()
            subprocess_env["PYTHONUNBUFFERED"] = "1"
            self._subprocess = await asyncio.create_subprocess_exec(
                "linguaclaw",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=subprocess_env,
            )
        except FileNotFoundError:
            self.post_message(LinguaclawEvent("run.error", {
                "error": "linguaclaw command not found. Is it installed?",
            }))
            return

        # Read stderr in background to avoid buffer deadlock
        stderr_task = asyncio.create_task(self._read_stderr(worker))

        # Read JSONL events from stdout using chunk-based reading
        buffer = b""
        while not worker.is_cancelled and self._subprocess.stdout:
            try:
                chunk = await asyncio.wait_for(
                    self._subprocess.stdout.read(65536),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue

            if not chunk:
                break

            buffer += chunk

            while b"\n" in buffer:
                line_bytes, buffer = buffer.split(b"\n", 1)
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if worker.is_cancelled:
                    break

                self.post_message(LinguaclawEvent(
                    event.get("event", "unknown"), event
                ))

            if worker.is_cancelled:
                break

        if self._subprocess:
            await self._subprocess.wait()

        try:
            await stderr_task
        except asyncio.CancelledError:
            pass

        if not worker.is_cancelled:
            # If we have stderr output but no stdout events, post it as an error
            if self.stderr_output:
                self.post_message(LinguaclawEvent("run.error", {
                    "error": f"Subprocess stderr:\n{self.stderr_output}",
                }))
            self.post_message(LinguaclawEvent("run.finished", {}))

    async def _read_stderr(self, worker) -> None:
        """Collect stderr output silently (for debugging)."""
        buf: list[str] = []
        chunk_buf = b""
        while not worker.is_cancelled and self._subprocess and self._subprocess.stderr:
            try:
                chunk = await asyncio.wait_for(
                    self._subprocess.stderr.read(65536),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue
            if not chunk:
                break
            chunk_buf += chunk
            while b"\n" in chunk_buf:
                line, chunk_buf = chunk_buf.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    buf.append(text)
        if chunk_buf:
            text = chunk_buf.decode("utf-8", errors="replace").rstrip()
            if text:
                buf.append(text)
        if buf:
            self.stderr_output = "\n".join(buf)

    # ── Event handlers ──────────────────────────────────────────────

    async def on_linguaclaw_event(self, message: LinguaclawEvent) -> None:
        """Handle incoming events from the linguaclaw subprocess."""
        event_type = message.event_type
        data = message.data

        conversation = self.query_one("#conversation-pane", ConversationPane)
        details = self.query_one("#details-sidebar", DetailsSidebar)

        if event_type == "run.starting":
            details.set_status("starting")


        elif event_type == "run.started":
            details.set_model(data.get("model", self._model or "?"))
            harnesses = data.get("harnesses", [])
            if harnesses:
                details.set_harnesses(", ".join(str(h) for h in harnesses))
            details.set_status("running")

        elif event_type == "message.triggered":
            reason = str(data.get("reason", ""))
            role = str(data.get("role", "")).lower()
            step = data.get("step", 0)
            message_data = data.get("message", {})

            if reason == "system_prompt":
                return

            if reason == "compression_prompt":
                details.set_status("compressing")
                return

            if reason == "compression_summary":
                details.set_status("running")
                return

            if role == "user":
                # Already rendered via _start_run. Skip re-rendering the
                # raw prompt (which includes conversation history).
                pass
            elif role == "assistant":
                # Assistant messages are now rendered via model.chunk + model.completed.
                # Skip here to avoid duplicating the streaming output.
                pass
            elif role == "tool":
                conversation.add_tool_result(message_data)
                exit_code = self._extract_exit_code(message_data)
                tool_name = str(message_data.get("name", "tool"))
                runtime_val = self._extract_runtime(message_data)
                details.set_last_tool(tool_name, exit_code, runtime_val)

        elif event_type == "model.requested":
            details.set_status("thinking")
            # Prepare streaming pane for incoming chunks
            step = data.get("step", 0)
            streaming = self.query_one("#streaming-pane", StreamingPane)
            streaming.start_streaming()

        elif event_type == "model.chunk":
            delta = data.get("delta", "")
            streaming = self.query_one("#streaming-pane", StreamingPane)
            streaming.append(delta)

        elif event_type == "model.completed":
            usage = data.get("usage") or {}
            step = data.get("step", 0)
            details.set_step(step)
            details.set_token_usage(
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                usage.get("total_tokens", 0),
            )
            # Calculate and update cost estimate for this model call
            prompt_t = usage.get("prompt_tokens", 0)
            completion_t = usage.get("completion_tokens", 0)
            turn_cost = self._calculate_cost(self._model, prompt_t, completion_t)
            self._session_prompt_tokens += prompt_t
            self._session_completion_tokens += completion_t
            self._session_cost += turn_cost
            details.set_cost(turn_cost, self._session_cost)
            details.set_status("running")
            # Finalize streaming — if there was accumulated content, push it
            # to the conversation pane as a proper Panel
            streaming = self.query_one("#streaming-pane", StreamingPane)
            text = streaming.accumulated_text
            if text.strip():
                conversation.add_assistant_message(text, step)
            streaming.finish_streaming(text)

        elif event_type == "tool.started":
            tool = data.get("tool", "?")
            arguments = data.get("arguments", {})
            details.set_last_tool(tool, None, None)
            conversation.add_tool_call(tool, arguments)

        elif event_type == "context.compression_requested":
            usage_ratio = data.get("usage_ratio", 0)
            details.set_context_usage(usage_ratio)
            details.set_status("compressing")

        elif event_type == "context.compressed":
            usage_ratio = data.get("usage_ratio", 0)
            details.set_context_usage(usage_ratio)
            details.set_status("running")

        elif event_type == "run.completed":
            details.set_status("completed")
            usage = data.get("usage") or {}
            details.set_token_usage(
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                usage.get("total_tokens", 0),
            )
            # Calculate and update cost estimate for final run metrics
            prompt_t = usage.get("prompt_tokens", 0)
            completion_t = usage.get("completion_tokens", 0)
            turn_cost = self._calculate_cost(self._model, prompt_t, completion_t)
            self._session_prompt_tokens += prompt_t
            self._session_completion_tokens += completion_t
            self._session_cost += turn_cost
            details.set_cost(turn_cost, self._session_cost)
            summary = data.get("summary", "")
            self._last_assistant_summary = summary
            total_calls = data.get("total_tool_calls", 0)
            conversation.add_system_message(
                f"Run completed. Tool calls: {total_calls}. "
                f"{summary[:200] + '...' if len(summary) > 200 else summary}"
            )

        elif event_type == "run.failed":
            reason = data.get("reason", "unknown")
            error = data.get("error", "")
            details.set_status("failed")
            streaming = self.query_one("#streaming-pane", StreamingPane)
            streaming.cancel_streaming()
            conversation.add_system_message(f"Run failed: {reason}  {error}")

        elif event_type == "run.error":
            details.set_status("error")
            streaming = self.query_one("#streaming-pane", StreamingPane)
            streaming.cancel_streaming()
            conversation.add_system_message(f"Error: {data.get('error', '')}")

        elif event_type == "run.finished":
            # Show stderr diagnostic when run failed
            if self.stderr_output and details.status in ("failed", "error"):
                conv.add_system_message(f"[DIAG] stderr (Ctrl+E to copy all):")
                for line in self.stderr_output.split("\n"):
                    conv.add_system_message(line)

            # Save assistant's summary to conversation history
            if details.status == "completed" and self._last_assistant_summary:
                self.conversation_history.append({
                    "role": "assistant",
                    "content": self._last_assistant_summary,
                })
            elif details.status not in ("completed", "failed", "error"):
                details.set_status("finished")

            self._on_run_finished()

    # ── Helpers ─────────────────────────────────────────────────────

    def _extract_exit_code(self, message_data: dict) -> int | None:
        content = message_data.get("content")
        try:
            payload = json.loads(content) if isinstance(content, str) else {}
            return payload.get("exit_code")
        except (json.JSONDecodeError, TypeError):
            return None

    def _extract_runtime(self, message_data: dict) -> float | None:
        content = message_data.get("content")
        try:
            payload = json.loads(content) if isinstance(content, str) else {}
            runtime = payload.get("runtime")
            if runtime is not None:
                return round(float(runtime), 1)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return None

    @staticmethod
    def _calculate_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Estimate cost using litellm's pricing map.

        Falls back to 0.0 if the model isn't in litellm's registry.
        """
        if not model_id or not prompt_tokens or not completion_tokens:
            return 0.0
        try:
            info = litellm.get_model_info(model_id)
            return (prompt_tokens * info["input_cost_per_token"]) + (
                completion_tokens * info["output_cost_per_token"]
            )
        except Exception:
            try:
                info = litellm.get_model_info(f"anthropic/{model_id}")
                return (prompt_tokens * info["input_cost_per_token"]) + (
                    completion_tokens * info["output_cost_per_token"]
                )
            except Exception:
                return 0.0

    # ── Actions ─────────────────────────────────────────────────────

    async def action_interrupt(self) -> None:
        """Send SIGINT to the linguaclaw subprocess."""
        if self._subprocess and self._subprocess.returncode is None:
            self._subprocess.send_signal(signal.SIGINT)
            details = self.query_one("#details-sidebar", DetailsSidebar)
            details.set_status("interrupted")
            streaming = self.query_one("#streaming-pane", StreamingPane)
            streaming.cancel_streaming()
            conversation = self.query_one("#conversation-pane", ConversationPane)
            conversation.add_system_message("Run interrupted by user.")

    def action_quit(self) -> None:
        """Quit the app."""
        if self._subprocess and self._subprocess.returncode is None:
            self._subprocess.send_signal(signal.SIGTERM)
        self.exit()

    def action_show_stderr(self) -> None:
        """Copy stderr to clipboard and show it."""
        stderr = self.stderr_output or "(no stderr output)"
        try:
            self.copy_to_clipboard(stderr)
            self.notify(f"Stderr copied to clipboard ({len(stderr)} chars)", timeout=5)
        except Exception as e:
            self.notify(f"Stderr (copy failed: {e}):\n{stderr[:200]}", timeout=10)
