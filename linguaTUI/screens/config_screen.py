"""
Startup configuration screen for linguaTUI.

Shown when --model and/or a --harness artifact haven't been specified on the CLI.
Simplified for v0.3: users pick a model + agent (artifact). Modules are loaded
automatically by default, hidden behind an Advanced toggle for power users who
want to disable specific ones.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import (
    Button,
    Collapsible,
    Input,
    Label,
    RadioButton,
    RadioSet,
    SelectionList,
    Static,
)


def find_harnesses() -> dict[str, list[dict[str, str]]]:
    """Scan the linguaclaw installation for available harnesses.

    Returns two groups: "artifacts" and "modules", each a list of
    dicts with keys: name, type, label.
    """
    groups: dict[str, list[dict[str, str]]] = {
        "artifacts": [],
        "modules": [],
    }

    spec = importlib.util.find_spec("linguaclaw")
    if not spec or not spec.origin:
        return groups

    project_root = Path(spec.origin).resolve().parents[1]
    harnesses_dir = project_root / "harnesses"

    if not harnesses_dir.exists():
        return groups

    for subdir in ("artifacts", "modules"):
        dirpath = harnesses_dir / subdir
        if not dirpath.exists():
            continue
        for item in sorted(dirpath.iterdir()):
            if item.is_dir() and (item / "SKILL.md").exists():
                groups[subdir].append({
                    "name": item.name,
                    "type": subdir,
                })

    return groups


def find_harnesses_flat() -> list[dict[str, str]]:
    """Flat list for backward compat — used by tests."""
    result: list[dict[str, str]] = []
    for group in find_harnesses().values():
        result.extend(group)
    return result


def read_model_default() -> str:
    """Get the default model from os.environ or .env files."""
    val = os.environ.get("LINGUACLAW_MODEL", "")
    if val:
        return val

    for candidate in [
        Path.cwd() / ".env",
        Path.home() / "linguaTUI" / "Linguaclaw" / ".env",
    ]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if line.startswith("LINGUACLAW_MODEL="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    return ""


# Brief descriptions for known artifacts — helps users choose without
# needing to understand the harness taxonomy.
_ARTIFACT_DESCRIPTIONS: dict[str, str] = {
    "trae-agent": "General-purpose coding agent (default)",
    "swe-agent": "Structured benchmark-style agent (SWE-bench)",
    "live-swe-agent": "Interactive live coding agent",
}


class ConfigScreen(Screen[dict]):
    """Startup config screen — simplified for v0.3.

    Shows model + agent picker by default. Modules and workspace are
    tucked behind an "Advanced" Collapsible.
    """

    CSS = """
    ConfigScreen {
        align: center middle;
    }

    #config-panel {
        width: 62;
        max-height: 90%;
        padding: 1 2;
        border: solid $secondary;
        background: $surface;
        overflow-y: auto;
    }

    #config-panel > .title {
        text-style: bold;
        margin-bottom: 1;
    }

    #config-panel .section-label {
        margin-top: 1;
        text-style: underline;
    }

    #config-panel .hint {
        color: $text-muted;
        text-style: italic;
        margin-bottom: 1;
    }

    #config-panel Input {
        width: 100%;
        margin-bottom: 1;
    }

    #artifact-set {
        height: auto;
        margin-bottom: 1;
        border: solid $border;
        padding: 0 1;
    }

    #artifact-set RadioButton {
        padding: 0 1;
    }

    .artifact-desc {
        color: $text-muted;
        margin-left: 2;
        margin-bottom: 1;
    }

    #module-list {
        height: auto;
        max-height: 10;
        border: solid $border;
        margin-bottom: 1;
        padding: 0 1;
    }

    #config-start-btn {
        width: 100%;
        margin-top: 1;
    }

    #config-skip-hint {
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
    }

    Collapsible {
        margin-top: 1;
        border: none;
    }

    CollapsibleTitle {
        text-style: bold;
        color: $secondary;
    }

    #advanced-content {
        margin: 1 0;
        padding: 0 1;
        border-left: solid $border;
    }
    """

    def __init__(
        self,
        default_model: str,
        harnesses: dict[str, list[dict[str, str]]],
        cli_harnesses: list[str],
        cli_workspace: str,
    ) -> None:
        self._default_model = default_model
        self._harness_groups = harnesses
        self._cli_harnesses = cli_harnesses
        self._cli_workspace = cli_workspace
        super().__init__()

    def compose(self) -> ComposeResult:
        artifacts = self._harness_groups.get("artifacts", [])
        modules = self._harness_groups.get("modules", [])

        with Static(id="config-panel"):
            yield Static("linguaTUI Configuration", classes="title")

            # ── Model (always shown) ─────────────────────────────
            yield Label("Model:", classes="section-label")
            yield Input(
                id="config-model",
                value=self._default_model,
                placeholder="e.g. deepseek/deepseek-v4-flash",
            )

            # ── Agent / Artifact (always shown) ──────────────────
            if artifacts:
                yield Label("Agent:", classes="section-label")
                preselected = (
                    self._cli_harnesses[0]
                    if self._cli_harnesses
                    else artifacts[0]["name"]
                )
                with RadioSet(id="artifact-set"):
                    for a in artifacts:
                        yield RadioButton(
                            a["name"],
                            id=f"artifact-{a['name']}",
                        )
                # Show descriptions for known artifacts
                for a in artifacts:
                    desc = _ARTIFACT_DESCRIPTIONS.get(a["name"])
                    if desc:
                        yield Static(
                            desc,
                            classes="artifact-desc",
                            id=f"desc-{a['name']}",
                        )
            else:
                yield Label("Agent:", classes="section-label")
                yield Input(
                    id="config-artifact-input",
                    placeholder='e.g. "trae-agent"',
                )

            # ── Advanced: modules + workspace ────────────────────
            with Collapsible(title="Advanced", collapsed=True):
                with Static(id="advanced-content"):
                    if modules:
                        yield Label("Modules (disable any you don't need):", classes="section-label")
                        yield Static(
                            "All enabled by default — uncheck to exclude from context.",
                            classes="hint",
                        )
                        yield SelectionList(
                            *[
                                (m["name"], m["name"], True)  # all selected by default
                                for m in modules
                            ],
                            id="module-list",
                        )
                    else:
                        yield Label("Modules:", classes="section-label")
                        yield Input(
                            id="config-modules-input",
                            placeholder='comma separated, e.g. "file-backed-state, verifier"',
                        )

                    yield Label("Workspace:", classes="section-label")
                    yield Input(
                        id="config-workspace",
                        value=self._cli_workspace,
                        placeholder="Path to workspace directory",
                    )

            yield Button("Start Conversation", id="config-start-btn", variant="primary")

            yield Static(
                "Tip: you can also run linguaTUI --model ... --harness ... "
                "to skip this screen.",
                id="config-skip-hint",
            )

    def on_mount(self) -> None:
        """Post-mount: select the right artifact button."""
        artifacts = self._harness_groups.get("artifacts", [])
        if not artifacts:
            return

        target = (
            self._cli_harnesses[0]
            if self._cli_harnesses
            else artifacts[0]["name"]
        )
        radio_set = self.query_one("#artifact-set", RadioSet)
        for btn in radio_set.query(RadioButton):
            if btn.id == f"artifact-{target}":
                btn.value = True
                break

    def on_button_pressed(self, event: Button.Pressed) -> None:
        artifacts = self._harness_groups.get("artifacts", [])
        modules = self._harness_groups.get("modules", [])

        model = self.query_one("#config-model", Input).value.strip()

        harnesses: list[str] = []

        # Gather artifact selection
        if artifacts:
            radio_set = self.query_one("#artifact-set", RadioSet)
            pressed = radio_set.pressed_button
            if pressed is not None:
                harnesses.append(str(pressed.label))
        else:
            raw = self.query_one("#config-artifact-input", Input).value.strip()
            if raw:
                harnesses.append(raw)

        # Gather module selection (all selected by default)
        if modules:
            module_list = self.query_one("#module-list", SelectionList)
            harnesses.extend(str(v) for v in module_list.selected)
        else:
            raw = self.query_one("#config-modules-input", Input).value.strip()
            harnesses.extend(h.strip() for h in raw.split(",") if h.strip())

        workspace = self.query_one("#config-workspace", Input).value.strip() or "."

        self.dismiss({
            "model": model or "",
            "harnesses": harnesses,
            "workspace": workspace,
        })
