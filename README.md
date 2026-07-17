# TheCode

**AI Software Engineer for your terminal.**

TheCode is a provider-independent coding agent with a full-screen terminal workbench. It can understand a repository, search and read code, propose plans, edit files with approval, run commands and tests, inspect Git changes, and preserve local sessions.

[![PyPI version](https://img.shields.io/pypi/v/thecode-agent.svg)](https://pypi.org/project/thecode-agent/)
[![Python](https://img.shields.io/pypi/pyversions/thecode-agent.svg)](https://pypi.org/project/thecode-agent/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> TheCode can modify files and execute approved commands. Use version control, review permission prompts, and never share API keys.

## Highlights

- Premium Textual and Rich terminal interface
- Streaming assistant responses and live tool activity
- Preview, Diff, Files, Architecture, Terminal, and Logs panels
- Tool inspector with arguments, output, status, and duration
- Permission modes for safe, interactive, or more autonomous work
- OpenRouter, OpenAI, Anthropic, and local Ollama support
- Git-aware repository analysis and session history
- Multi-folder `.code-workspace` support
- `@file` context, project rules, skills, and `AGENTS.md` support
- Sensitive-file protection for `.env`, private keys, and credentials
- Windows, Linux, and macOS support

## Requirements

- Python 3.12 or newer
- Git
- [Ripgrep](https://github.com/BurntSushi/ripgrep) is recommended for fast search
- An API key for OpenRouter, OpenAI, or Anthropic; Ollama can run locally without one

## Install

The recommended installation uses `pipx`, which keeps CLI applications isolated:

```bash
pipx install thecode-agent
```

If `pipx` is not installed:

```bash
python -m pip install --user pipx
python -m pipx ensurepath
```

Alternatively, install with pip:

```bash
python -m pip install thecode-agent
```

Upgrade later with:

```bash
pipx upgrade thecode-agent
# or: python -m pip install --upgrade thecode-agent
```

## First run

Open a terminal in the project you want TheCode to work on:

```bash
cd path/to/your/project
thecode config --setup
thecode doctor
thecode
```

The setup wizard asks for a provider, model, API key, and permission mode. Credentials are entered with hidden input and stored in the project `.env`; that file should remain ignored by Git.

Inside the workbench, type a request:

```text
Create a small REST API for managing fruits and add tests.
```

Type `/` to discover interactive commands. Use `@path/to/file.py` to attach a specific file to the request.

## Providers

| Provider | Credential | Example model |
| --- | --- | --- |
| OpenRouter | `OPENROUTER_API_KEY` | `anthropic/claude-sonnet-4` |
| OpenAI | `OPENAI_API_KEY` | Provider model identifier |
| Anthropic | `ANTHROPIC_API_KEY` | Provider model identifier |
| Ollama | No key required | Any locally installed model |

Run the setup wizard again at any time:

```bash
thecode config --setup
```

Never commit `.env` or paste credentials into chat, issues, logs, or documentation. Revoke any key that has been exposed.

## Workbench controls

| Key | Action |
| --- | --- |
| `Enter` | Send the request |
| `Ctrl+P` | Quick Open a file |
| `Ctrl+K` | Open the command center |
| `Ctrl+T` | Ask the agent to run relevant tests |
| `Ctrl+R` | Run the previous request again |
| `Ctrl+L` | Clear the conversation view |
| `Esc` | Cancel the current operation |
| `Ctrl+Q` | Exit TheCode |

When typing `/`, suggestions are filtered as you type. Press `Down` and `Enter`, click an option, or press `Tab` to accept the first suggestion.

Useful interactive commands:

```text
/commands
/config
/models
/architect OBJECTIVE
/theme NAME
/workspace NAME
/processes
/session
/clear
/exit
```

`/architect` performs a read-only Current/Proposed architecture analysis. TheCode only starts implementation after you select **Apply architecture**.

## CLI commands

The full-screen workbench is the default, but commands can also be run directly:

```bash
thecode
thecode chat
thecode chat --classic
thecode ask "How does authentication work?"
thecode plan "Migrate JWT authentication to OAuth2"
thecode run "Fix the failing tests"
thecode review
thecode init
thecode config
thecode models
thecode sessions
thecode resume SESSION_ID
thecode undo SESSION_ID
thecode delete-session SESSION_ID
thecode theme
```

## Permission modes

- `safe`: reads are automatic; writes and commands require confirmation.
- `ask`: reads are automatic; mutating actions request approval.
- `auto`: lower-risk operations can run automatically; high-risk actions still ask and critical actions remain blocked.

Permission prompts support **Allow once**, **Allow for this session**, and **Deny**. Destructive and critical operations remain restricted.

## Project instructions and context

Initialize optional project configuration and an instruction file:

```bash
thecode init
```

This creates `.nexus/config.yaml` and `NEXUS.md` without overwriting existing files. TheCode also understands common agent instructions including `AGENTS.md`, `AGENT.md`, `CLAUDE.md`, `.cursorrules`, Cursor rules, and `SKILL.md` files.

Configuration is loaded from `~/.nexus/config.yaml`, then overridden by the current project's `.nexus/config.yaml`.

Example:

```yaml
llm:
  provider: openrouter
  model: anthropic/claude-sonnet-4
agent:
  max_steps: 30
permissions:
  mode: ask
context:
  max_characters: 120000
```

## Workspaces

TheCode detects a `.code-workspace` file and supports multiple project folders:

```bash
thecode workspace open platform.code-workspace
thecode workspace list platform.code-workspace
thecode workspace add ../backend --path platform.code-workspace
```

Inside the workbench, use `/workspace NAME` to switch the active folder.

## Sessions and recovery

Sessions are stored locally in `~/.nexus/sessions.db`.

```bash
thecode sessions
thecode resume SESSION_ID
thecode undo SESSION_ID
thecode delete-session SESSION_ID
```

`undo` refuses to overwrite a file that was subsequently changed by the user.

## Troubleshooting

Run diagnostics first:

```bash
thecode doctor
```

If the `thecode` command is not found, restart the terminal after installing with `pipx`, or run:

```bash
python -m pipx ensurepath
```

If an older editable installation takes precedence, uninstall the previous package and reinstall:

```bash
python -m pip uninstall thecode-agent -y
python -m pip install --upgrade thecode-agent
```

For a clean onboarding reset, close TheCode first and remove its local state. This does not remove your source code:

```powershell
# PowerShell
Remove-Item -Recurse -Force .nexus -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$HOME\.nexus" -ErrorAction SilentlyContinue
thecode config --setup
```

API keys stored in `.env` or operating-system environment variables are not deleted by this reset.

## Security

Paths are resolved against the active project, including symlinks. Sensitive files are hidden from automatic previews, Quick Open, project trees, and `@file` attachments. Commands use argument arrays rather than `shell=True`; unsupported shell operators and destructive patterns are blocked. File changes preserve recovery metadata.

Security reports should not contain live credentials, private keys, or proprietary source code.

## Development

Clone the repository and install the development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Run the quality checks:

```bash
ruff check nexus tests
mypy nexus
pytest
```

HTTP provider tests use mocked transports and do not require live API access.

## License

TheCode is available under the [MIT License](LICENSE).
