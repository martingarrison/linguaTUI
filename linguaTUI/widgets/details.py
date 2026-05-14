"""
Details sidebar — shows context usage, step, tokens, cost, tool info, model, etc.
"""

from __future__ import annotations

from rich.console import Group
from rich.progress_bar import ProgressBar
from rich.text import Text
from textual.widgets import Static
from textual.widget import Widget


class DetailsSidebar(Widget):
    """Right-side panel showing run details."""

    def __init__(self, **kwargs):
        self.status = "idle"
        self._cumulative_cost = 0.0
        super().__init__(**kwargs)

    def compose(self):
        yield Static("", id="details-status", classes="details-section")
        yield Static("", id="details-model", classes="details-section")
        yield Static("", id="details-harness", classes="details-section")
        yield Static("", id="details-steps", classes="details-section")
        yield Static("", id="details-tokens", classes="details-section")
        yield Static("", id="details-cost", classes="details-section")
        yield Static("", id="details-context", classes="details-section")
        yield Static("", id="details-tool", classes="details-section")

    def _section(self, widget_id: str) -> Static:
        return self.query_one(f"#{widget_id}", Static)

    def set_status(self, status_text: str) -> None:
        """Update the status indicator."""
        self.status = status_text
        widget = self._section("details-status")
        icon = {
            "idle": "○", "starting": "●", "running": "▶",
            "thinking": "⟳", "compressing": "⚙",
            "completed": "✓", "failed": "✗",
            "interrupted": "⊘", "error": "⚠", "finished": "✓",
        }.get(status_text, "?")
        color = {
            "idle": "dim", "starting": "cyan", "running": "green",
            "thinking": "yellow", "compressing": "blue",
            "completed": "green", "failed": "red",
            "interrupted": "yellow", "error": "red", "finished": "green",
        }.get(status_text, "white")
        t = Text(f"{icon}  {status_text.upper()}", style=f"bold {color}")
        widget.update(t)

    def set_model(self, model: str) -> None:
        """Update the model name display."""
        widget = self._section("details-model")
        t = Text()
        t.append("Model\n", style="bold")
        t.append(model, style="cyan")
        widget.update(t)

    def set_harnesses(self, harnesses: str) -> None:
        """Update harness names."""
        widget = self._section("details-harness")
        t = Text()
        t.append("Harness\n", style="bold")
        t.append(harnesses or "—", style="dim")
        widget.update(t)

    def set_step(self, step: int) -> None:
        """Update the step counter."""
        widget = self._section("details-steps")
        t = Text()
        t.append("Step\n", style="bold")
        t.append(str(step), style="bold white")
        widget.update(t)

    def set_token_usage(self, prompt_tokens: int, completion_tokens: int, total_tokens: int) -> None:
        """Update token usage display."""
        widget = self._section("details-tokens")
        t = Text()
        t.append("Tokens\n", style="bold")
        t.append(f"P: {prompt_tokens:,}  C: {completion_tokens:,}\n", style="dim")
        t.append(f"Total: {total_tokens:,}", style="white")
        widget.update(t)

    def set_cost(self, turn_cost: float, cumulative_cost: float) -> None:
        """Update cost display — per-turn estimate and cumulative session cost."""
        self._cumulative_cost = cumulative_cost
        widget = self._section("details-cost")
        t = Text()
        t.append("Cost\n", style="bold")
        if turn_cost > 0:
            t.append(f"Turn: ${turn_cost:.4f}\n", style="yellow")
        else:
            t.append("Turn: —\n", style="dim")
        t.append(f"Session: ${cumulative_cost:.4f}", style="green" if cumulative_cost > 0 else "dim")
        widget.update(t)

    def set_context_usage(self, ratio: float) -> None:
        """Update the context usage progress bar."""
        widget = self._section("details-context")
        ratio = min(max(ratio, 0.0), 1.0)
        pct = int(ratio * 100)
        bar = ProgressBar(total=100, completed=pct, width=20)
        remaining = 100 - pct
        color = "red" if ratio > 0.8 else "yellow" if ratio > 0.6 else "green"
        t = Text()
        t.append("Context Window\n", style="bold")
        t.append(f"{pct}% used ({remaining}% free)\n", style=color)
        widget.update(Group(t, bar))

    def set_last_tool(self, tool: str, exit_code: int | None, runtime: float | None) -> None:
        """Update the last tool call info."""
        widget = self._section("details-tool")
        t = Text()
        t.append("Last Tool\n", style="bold")
        t.append(f"{tool}\n", style="yellow")
        parts = []
        if exit_code is not None:
            color = "green" if exit_code == 0 else "red"
            parts.append(f"exit: {exit_code}")
        if runtime is not None:
            parts.append(f"runtime: {runtime}s")
        if parts:
            t.append("  ".join(parts), style="dim")
        widget.update(t)
