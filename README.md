# linguaTUI

A Textual-based TUI for [linguaclaw](https://github.com/agentics-org/linguaclaw) — a thin agent harness runtime.

## Quick Start

```bash
# Interactive mode (shows config screen if model/harness not specified)
linguaTUI

# Or pass everything on the command line
linguaTUI --model deepseek/deepseek-v4-flash --harness trae-agent
```

## Requirements

- Python 3.10+
- linguaclaw installed (editable install recommended)
- API key in `LINGUACLAW_API_KEY` env var or `.env` file in linguaclaw root

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `Ctrl+C` | Interrupt running agent |
| `Ctrl+E` | Copy subprocess stderr to clipboard |
| `Ctrl+Q` / `q` | Quit |

## Architecture

One subprocess per turn. TUI manages conversation memory and feeds context forward.
No persistent connection — clean separation, works with any installed linguaclaw version.

## Related: pi-web-access

This repo includes a cloned copy of the `pi-web-access` npm package (v0.10.7) at
[`pi-web-access/`](./pi-web-access/) — source:
<https://github.com/nicobailon/pi-web-access>. It provides web search, URL fetching,
code search, YouTube/video extraction, and more as a Pi coding agent extension.
Loaded via `~/.pi/agent/settings.json` as `"npm:pi-web-access"`.
