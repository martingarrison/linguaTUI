"""
Conversation pane — renders the message history as a scrollable log.

Includes a StreamingPane for showing assistant responses token-by-token
as they're generated.
"""

from __future__ import annotations

import json

from rich.console import Group, RenderableType
from rich.markdown import Markdown as RichMarkdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import RichLog, Static


# ── Truncation constants ──────────────────────────────────────────────

MAX_STDOUT_LINES = 25
"""Maximum lines of stdout to display in a tool result panel.
Additional lines are replaced by a truncation notice."""

MAX_STDERR_LINES = 15
"""Maximum lines of stderr to display in a tool result panel."""


def _truncate(text: str, max_lines: int) -> tuple[str, bool]:
    """Truncate *text* to at most *max_lines* lines.

    Returns ``(display_text, was_truncated)`` where *display_text*
    includes a truncation notice when the original exceeds the limit.
    """
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False
    head = "\n".join(lines[:max_lines])
    omitted = len(lines) - max_lines
    # Count total chars for the notice too
    total_chars = len(text)
    note = f"\n[... {omitted} more line(s) ({total_chars} total chars) omitted for display]"
    return head + note, True


# ── Streaming pane ────────────────────────────────────────────────────

class StreamingPane(Static):
    """A pane that shows assistant response text as it streams in.

    Hidden by default. Made visible when streaming starts.
    Content is accumulated via append() and rendered progressively.
    """

    DEFAULT_CSS = """
    StreamingPane {
        display: none;
        height: auto;
        max-height: 60%;
        width: 100%;
        padding: 0 1 1 1;
        background: $surface;
        border-top: dashed $border;
        overflow-y: auto;
    }

    StreamingPane.-streaming {
        display: block;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._accumulated: list[str] = []

    def start_streaming(self) -> None:
        """Show the pane and prepare for incoming content."""
        self._accumulated = []
        self.add_class("-streaming")
        self.update("")

    def append(self, delta: str) -> None:
        """Append a text delta to the streaming content."""
        if not delta:
            return
        self._accumulated.append(delta)
        text = "".join(self._accumulated)
        # Show the accumulated text with a blinking cursor indicator
        display = Text(text, style="italic")
        display.append(" ▌", style="bold cyan")
        self.update(display)

    def finish_streaming(self, final_text: str) -> None:
        """Convert streaming content to a final message and hide the pane.

        Returns the accumulated text for the caller to render as a Panel.
        """
        self.remove_class("-streaming")
        self.update("")
        self._accumulated = []

    def cancel_streaming(self) -> None:
        """Clear streaming state without finalizing (e.g. on error)."""
        self.remove_class("-streaming")
        self.update("")
        self._accumulated = []

    @property
    def accumulated_text(self) -> str:
        return "".join(self._accumulated)


# ── Conversation pane ─────────────────────────────────────────────────

class ConversationPane(RichLog):
    """A scrollable log of conversation messages."""

    def __init__(self, **kwargs):
        super().__init__(highlight=True, markup=True, max_lines=None, **kwargs)

    def add_user_message(self, content: str) -> None:
        """Append a user message to the conversation."""
        if not content.strip():
            return
        panel = Panel(
            RichMarkdown(content),
            title="USER",
            title_align="left",
            border_style="blue",
            padding=(1, 2),
        )
        self.write(panel)
        self.scroll_end(animate=False)

    def add_assistant_message(self, content: str, step: int) -> None:
        """Append an assistant message to the conversation."""
        if not content.strip():
            return
        panel = Panel(
            RichMarkdown(content),
            title=f"ASSISTANT (step {step})",
            title_align="left",
            border_style="green",
            padding=(1, 2),
        )
        self.write(panel)
        self.scroll_end(animate=False)

    def add_tool_call(self, tool: str, arguments: dict) -> None:
        """Add a tool call notification (before result arrives)."""
        cmd = arguments.get("cmd", "")
        text = Text()
        text.append(f"$ {cmd}", style="bold yellow")
        timeout = arguments.get("timeout_seconds")
        if timeout:
            text.append(f"  (timeout: {timeout}s)", style="dim")
        panel = Panel(
            text,
            title=f"TOOL CALL: {tool}",
            title_align="left",
            border_style="yellow",
            padding=(0, 1),
        )
        self.write(panel)

    def add_tool_result(self, message_data: dict) -> None:
        """Append a tool result to the conversation.

        Stdout and stderr are truncated to :data:`MAX_STDOUT_LINES` and
        :data:`MAX_STDERR_LINES` lines respectively to avoid overwhelming
        the conversation view.  A truncation notice is appended when
        content exceeds those limits.
        """
        tool_name = str(message_data.get("name") or "tool")
        content = message_data.get("content")
        try:
            payload = json.loads(content) if isinstance(content, str) else {}
        except json.JSONDecodeError:
            payload = {}

        parts: list[RenderableType] = []

        # ── Status line ──
        exit_code = payload.get("exit_code", "?")
        runtime = payload.get("runtime", "?")
        timed_out = payload.get("timed_out", False)
        status_symbol = "✓" if exit_code == 0 else "✗"
        status_text = Text(
            f"{status_symbol} exit: {exit_code}  runtime: {runtime}s"
        )
        if timed_out:
            status_text.append("  ⏱ TIMED OUT", style="bold red")
        parts.append(status_text)

        # ── Stdout (truncated) ──
        stdout = str(payload.get("stdout", "")).strip()
        if stdout:
            display_stdout, was_truncated = _truncate(stdout, MAX_STDOUT_LINES)
            parts.append(Text("\nstdout:"))
            parts.append(Syntax(
                display_stdout, "bash",
                theme="monokai", word_wrap=True,
            ))

        # ── Stderr (truncated) ──
        stderr = str(payload.get("stderr", "")).strip()
        if stderr:
            display_stderr, _ = _truncate(stderr, MAX_STDERR_LINES)
            parts.append(Text("\nstderr:", style="red"))
            parts.append(Syntax(
                display_stderr, "bash",
                theme="monokai", word_wrap=True,
            ))

        panel = Panel(
            Group(*parts) if len(parts) > 1 else parts[0],
            title=f"TOOL RESULT: {tool_name}",
            title_align="left",
            border_style="bright_yellow",
            padding=(1, 2),
        )
        self.write(panel)
        self.scroll_end(animate=False)

    def add_system_message(self, text: str) -> None:
        """Add a system/info message (e.g. run completed, interrupted)."""
        panel = Panel(
            Text(text, style="italic dim"),
            title="SYSTEM",
            title_align="left",
            border_style="cyan",
            padding=(0, 1),
        )
        self.write(panel)
        self.scroll_end(animate=False)
