# linguaTUI — Plan

A Textual-based TUI that wraps linguaclaw, giving a rich interactive interface
for running and reviewing agent sessions that *feels like Claude Code*.

**Status:** v0.3 in progress. [EVENT] markers removed, config screen simplified.
**North Star:** Zero-friction agent chat — type a prompt, see the agent work in real time,
pay attention to what matters, ignore what doesn't.

---

## Architecture (unchanged)

### Interface Strategy: Subprocess + `--json`

The TUI spawns `linguaclaw run --json ...` as a child process and consumes its
JSONL event stream from stdout. One subprocess per turn.

- The `--json` flag was designed for machine consumers.
- Zero coupling to linguaclaw internals — works with any installed version.
- The event schema (~17 event types) is the contract.

### Framework: Textual

- Same ecosystem as Rich (Textualize).
- Reactive widget tree, CSS-like styling (TCSS), async event loop.
- Worker API for subprocess management.

---

## Current Layout

```
~/linguaTUI/
├── PLAN.md                      ← This file
├── README.md                    ← Minimal docs
├── pyproject.toml               ← Package config
├── linguaTUI/                   ← Python package
│   ├── __init__.py
│   ├── __main__.py              ← CLI parser, creates LinguaTUIApp
│   ├── app.py                   ← Main Textual App, subprocess worker
│   ├── screens/
│   │   ├── __init__.py
│   │   └── config_screen.py     ← Startup config (model, artifacts, modules)
│   ├── widgets/
│   │   ├── __init__.py
│   │   ├── conversation.py      ← ConversationPane (RichLog-based)
│   │   └── details.py           ← DetailsSidebar (status, tokens, context)
│   └── assets/
│       └── linguaTUI.tcss       ← Stylesheet
├── pi-web-access/               ← Cloned pi-web-access npm package source
│                                   (github.com/nicobailon/pi-web-access)
│                                   Used by Pi as extension via "npm:pi-web-access"
│                                   in ~/.pi/agent/settings.json
│                                   Globally installed: ~/.nvm/.../node_modules/pi-web-access/
└── Linguaclaw/                  ← Cloned linguaclaw repo (patched for DeepSeek reasoning support)
```

---

## v0.2 Feature Set (current)

### Startup Config Screen
- Model selection (text Input, reads `LINGUACLAW_MODEL` from env as default)
- **Artifacts** (single-select via `RadioSet`): `trae-agent`, `swe-agent`, `live-swe-agent`
- **Modules** (multi-select via `SelectionList`): `dynamic-subagent-orchestration`, `evidence-protocol`, `file-backed-state`, `multi-candidate-search`, `self-evolution`, `verifier`
- Workspace input (defaults to `.`)
- Fallback text Inputs if no harnesses found on disk

### Interactive Multi-Turn Conversation
- User types prompts into a docked `Input` widget at the bottom
- Each turn spawns a fresh subprocess with conversation history fed as context
- TUI manages memory: captures assistant's final response, appends to history, includes prior turns in next prompt
- No LLM-based summarization — raw text for determinism and speed

### Live Run View
- **Conversation pane** (left, ~70%): user prompts, assistant responses, tool calls/results
- **Details sidebar** (right, ~30%): status, model, harness, step, tokens, context bar, last tool
- Debug `[EVENT]` markers in conversation pane (leftover from v0.1 diagnostics)

### Keyboard Bindings
- `Ctrl+C` — SIGINT to subprocess (interrupt agent mid-run)
- `Ctrl+E` — Show/copy stderr to clipboard
- `Ctrl+Q` / `q` — Quit

---

## v0.3 — The Claude Code Gap Analysis

The fundamental gaps between current linguaTUI and a Claude Code-like experience,
ordered by impact-to-effort ratio:

### v0.3 Completed

1. **Kill `[EVENT]` markers** ✅
   - Removed `conversation.add_system_message(f"[EVENT] {event_type}")`.
   - Stderr diagnostic now shows only on failure, not on every run.

2. **Simplify config screen** ✅
   - Modules loaded automatically by default (all checked in SelectionList).
   - Modules + workspace hidden behind "Advanced" Collapsible.
   - Artifact descriptions shown inline (`trae-agent` = General-purpose coding, etc.).
   - First-time UX: pick model, pick agent type, go. No taxonomy lecture.

3. **Streaming assistant output** ✅
   - New `model.chunk` event type emitted per delta from `completion(stream=True)`.
   - `model_gateway.py`: added `generate_stream()` method using `litellm.stream_chunk_builder`.
   - `runtime.py`: replaced blocking `generate()` with `generate_stream()`, emits `model.chunk` events.
   - `conversation.py`: added `StreamingPane` widget (Static-based, hidden by default).
   - `app.py`: handles `model.chunk` events, routes to StreamingPane for progressive
     rendering. Finalizes to Rich Panel on `model.completed`.
   - Cancels streaming on interrupt, error, or failure.
   - Rich CLI unaffected — ignores unknown event types.

### v0.3 Remaining (ordered by impact)

4. **Cost tracking** (~30 min)
   - Use `litellm`'s cost map to convert token counts into estimated dollar cost.
   - Display in sidebar next to token counts + cumulative session cost.

5. **Reopen config screen mid-session** (~1 hr)
   - `Ctrl+P` pops the config screen again (model, agent selection).
   - Pre-fill with current values. Changes on next turn, no restart.

6. **Diff-aware rendering** (~1-2 hrs)
   - Detect `diff --git` in tool results, render with diff syntax highlighting.

### Longer-Term

7. **Run history browser** — List past runs from `.linguaclaw/runs/`.
8. **Long conversation handling** — Token-count-based sliding window or rely on built-in compression.
9. **Session persistence** — Save/load conversations as named sessions.

---

## Config Screen Philosophy (revised)

**Old model (v0.2):** The config screen exposed linguaclaw's internal taxonomy
(artifacts vs. modules) because that's how the filesystem is organized. Users
had to understand the difference to start a conversation.

**New model (v0.3):** The config screen should ask the user what matters to *them*:

| What matters to the user | How the TUI handles it |
|---|---|
| Which model? | Always shown, defaults from `LINGUACLAW_MODEL` |
| Which agent behavior? | Simple picker (artifacts with descriptions) |
| Do I need modules? | Loaded automatically by default. Hidden toggle for power users. |
| What workspace? | Defaults to `.`. Shown in Advanced. |

**Why this works:**
- Modules are additive by design — loading all of them can't conflict
  (if they did conflict, that's a bug in the SKILL.md design, not the TUI's problem).
- Artifacts genuinely differ in behavior (trae vs swe vs live-swe).
  Making the user choose one is meaningful.
- The artifact/module taxonomy is an implementation detail.
  Users think in terms of "what kind of agent do I want."

---

## Key Decisions Log

| Decision | Rationale |
|---|---|
| Subprocess per turn (not persistent `--interactive`) | Cleanest separation; TUI manages memory |
| v0 memory: raw assistant text, no summarization | Deterministic, fast, no LLM dependency |
| Harnesses concatenated (artifacts + modules are equal at runtime) | All SKILL.md files are loaded identically |
| Modules loaded by default in v0.3 | Additive design makes conflicts unlikely |
| `PYTHONUNBUFFERED=1` in subprocess env | Forces real-time JSONL streaming through pipe |
| No `env=` with `.env` loading — subprocess inherits parent env | Parent already has API keys |
| Patches to linguaclaw: `model_gateway.py` + `runtime.py` | DeepSeek reasoning_content support |
