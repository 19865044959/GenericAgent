# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Critical Rules

- **Worktree 不允许修改主干（main/master）。** 在 worktree 中只能在自己的分支上工作，禁止直接提交到主干分支。这条规则适用于所有项目，无例外。

## Project Overview

GenericAgent is a minimal (~3K lines of seed code) self-evolving autonomous agent framework. It grants any LLM system-level control over a local computer through 9 atomic tools and a ~100-line agent loop. Its core philosophy: **don't preload skills, evolve them** — each task execution is crystallized into reusable Skills stored in a layered memory system.

## Build & Development Commands

```bash
# Install (Python 3.10–3.13 only; 3.14 is incompatible with pywebview)
uv venv && uv pip install -e ".[ui]"

# Configure (one-time)
cp mykey_template.py mykey.py   # Fill in API keys; see template comments for session types
# Or run the guided configurator:
python assets/configure_mykey.py

# Launch frontends
python frontends/tuiapp_v2.py    # Terminal TUI (Textual, recommended for dev)
python frontends/qtapp.py        # Desktop GUI (PyQt5)
python frontends/stapp.py        # Streamlit web UI
python launch.pyw                # Webview desktop shell
python agentmain.py              # Bare CLI (accepts --input "prompt", --llm_no N)

# CLI dispatcher (ga_cli)
python -m ga_cli tui2            # Launch TUI v2
python -m ga_cli cli             # Launch CLI
python -m ga_cli gui             # Launch desktop GUI
python -m ga_cli hub             # Launch hub manager

# One-shot task mode (file I/O)
python agentmain.py --task <iodir>

# Reflect/monitoring modes
python agentmain.py --reflect reflect/scheduler.py    # Scheduled task runner
python agentmain.py --reflect reflect/autonomous.py   # Auto-trigger when idle
GOAL_STATE=temp/xxx.json python agentmain.py --reflect reflect/goal_mode.py  # Goal-driven continuous mode

# Running tests (benchmarks)
python benchmark/run_benchmark.py
```

## Architecture

### Core Pipeline

```
User Input → memory_auto.search_memory() → sys_prompt + L2 retrieval → agent_runner_loop() → GenericAgentHandler.dispatch() → tool execution → turn_end_callback (inject history, working memory, plan hints) → repeat
```

After session ends: `memory_auto.extract_facts()` → `auto_update_l2()` writes to `memory/global_mem.txt` + syncs `memory/vectors.json`.

### Key Modules

| File | Role |
|------|------|
| `agent_loop.py` | ~100-line agent loop. `BaseHandler` dispatches `do_<tool_name>` calls. `agent_runner_loop()` manages the conversation with the LLM — messages are per-turn only; full history lives in the LLM client backend. |
| `ga.py` | `GenericAgentHandler(BaseHandler)` implements all 9 tools (`do_code_run`, `do_file_read`, `do_file_write`, `do_file_patch`, `do_web_scan`, `do_web_execute_js`, `do_ask_user`, `do_update_working_checkpoint`, `do_start_long_term_update`). Also has `get_global_memory()` which builds the L0/L1/L2 memory context injected into every system prompt. |
| `agentmain.py` | `GenericAgent` orchestrator: task queue, LLM session lifecycle, memory auto-retrieval at prompt time, memory auto-extraction on session end. The `run()` method is the main loop — it retrieves memory, builds the system prompt, creates a handler, runs `agent_runner_loop`, then triggers `_do_extraction()`. |
| `llmcore.py` | LLM client abstraction. Supports native Claude API (`NativeClaudeSession`), OpenAI-compatible (`NativeOAISession`), legacy text-protocol sessions, and `MixinSession` for multi-backend fallback. Key functions: `reload_mykeys()` (hot-reloads `mykey.py`), `resolve_client()` (auto-detects session type from variable naming), `compress_history_tags()` (truncates old `<thinking>`/`<tool_use>` blocks), `trim_messages_history()` (context window management). |
| `memory_auto.py` | Memory automation: `search_memory()` retrieves relevant L2 facts (embedding → keyword fallback), `extract_facts()` uses LLM to extract persistent facts from conversation, `auto_update_l2()` writes to `global_mem.txt` with conflict detection and de-duplication, then syncs vectors. |
| `mykey.py` | **Not tracked in git** (in `.gitignore`). User's API keys and session configs. `agentmain.py` scans variables whose names contain `api`/`config`/`cookie` and auto-resolves session types from naming conventions (see template comments). |
| `simphtml.py` | Browser HTML simplification — strips hidden/floating/covered elements for token-efficient web perception. |
| `TMWebDriver.py` | Chrome DevTools Protocol (CDP) wrapper for real browser control with session preservation. |

### Layered Memory System

| Layer | Storage | Purpose |
|-------|---------|---------|
| **L0** | `assets/sys_prompt.txt` | Core behavioral rules, injected as system prompt |
| **L1** | `memory/global_mem_insight.txt` | Condensed insight index, injected into every prompt via `get_global_memory()` |
| **L2** | `memory/global_mem.txt` + `memory/vectors.json` | Persistent user facts, auto-retrieved/updated. Vector index (`vectors.json`) is a machine-readable cache of `global_mem.txt`, rebuildable from it. |
| **L3** | `memory/*.md` (SOPs) | Reusable task workflows. Agent reads these via `file_read` and follows them. |
| **L4** | `temp/model_responses/` (raw) → `memory/L4_raw_sessions/` (compressed) | Session archives, compressed by `L4_raw_sessions/compress_session.py`. |

Memory retrieval (per prompt): `search_memory()` → embedding semantic search (via `memory/embedder.py` + `memory/vectors.py`) → keyword-based fallback with Chinese synonym expansion.

Memory extraction (per session end): `extract_facts()` → LLM extracts structured facts → `auto_update_l2()` handles conflict/duplicate detection → syncs `vectors.json`.

### Tool System

Tools are defined in `assets/tools_schema.json` (or `_cn.json` for Chinese-model compatibility). Each tool maps to a `do_<name>` method on `GenericAgentHandler`. The `dispatch()` method in `BaseHandler` routes tool calls. Tools return `StepOutcome(data, next_prompt, should_exit)`.

The 9 atomic tools:
- `code_run` — Python/PowerShell/bash execution (Python runs as temp files with `code_run_header.py` prepended)
- `file_read` — Line-based reading with keyword search and file-not-found suggestions
- `file_write` — overwrite/append/prepend; content from `<file_content>` tags or code blocks
- `file_patch` — Unique-string find-and-replace; supports `{{file:path:start:end}}` references
- `web_scan` — Simplified HTML + tab list via CDP
- `web_execute_js` — Arbitrary JS execution in browser
- `ask_user` — Human-in-the-loop interrupt
- `update_working_checkpoint` — Short-term working memory (auto-injected each turn)
- `start_long_term_update` — Triggers L2/L3 memory update workflow

### Hook System

`plugins/hooks.py` provides an event system: `tool_before`, `tool_after`, `llm_before`, `llm_after`, `turn_before`, `turn_after`, `agent_before`, `agent_after`. Plugins in `plugins/` are auto-discovered (files not starting with `_`). Use `@register('event_name')` decorator.

### Reflect Modules (Background Monitors)

Files in `reflect/` are monitoring scripts loaded via `--reflect`. Each must expose `INTERVAL` (seconds), `ONCE` (bool), and `check()` → returns a prompt string to trigger the agent, or `None` to skip. Optional `init(a)` receives CLI args. Return `'/exit'` to terminate.

- `scheduler.py` — Cron-like task runner from `sche_tasks/` directory
- `autonomous.py` — Triggers when user is idle >30 min
- `goal_mode.py` — Continuous self-driven work until budget exhausted
- `agent_team_worker.py` — BBS-based multi-agent task collaboration
- `checklist_master.py` — MapReduce-style checklist decomposition

### Frontend Architecture

All frontends in `frontends/` instantiate `GenericAgent` from `agentmain.py` and feed prompts via `put_task(query)`, receiving streaming output through the returned queue. The agent runs in a background daemon thread.

- `conductor.py` — FastAPI-based multi-agent orchestration hub with WebSocket streaming, subagent spawning, and IM integration
- `tuiapp_v2.py` — Prompt-toolkit + Rich TUI with scrollback-first design
- `qtapp.py` — PyQt5 desktop GUI
- `stapp.py` / `stapp2.py` — Streamlit web interfaces
- IM bots: `tgapp.py`, `wechatapp.py`, `qqapp.py`, `fsapp.py`, `wecomapp.py`, `dingtalkapp.py`

## Key Design Patterns

- **History is in the LLM client, not the handler**: `self.llmclient.backend.history` holds the full conversation. The handler's `history_info` is a summarized version for context injection. New handlers get full history from the client.
- **Language auto-detection**: `agentmain.py` sets `GA_LANG` from system locale. Tool schemas and system prompts have `_cn`/`_en` variants.
- **Working directory**: `temp/` (cwd for code execution). The `cwd` variable in system prompts points here.
- **Turn-end injection**: `turn_end_callback()` injects working memory, plan hints, danger warnings at configurable turn intervals. External files `_stop`, `_keyinfo`, `_intervene` in the task directory allow runtime intervention.
- **Session hot-reload**: `mykey.py` changes are detected via mtime. LLM sessions auto-rotate with `/session.*=` commands.
- **Plan mode**: When agent enters plan mode (via plan SOP), `_in_plan_mode()` returns the plan file path, turn limits increase, and completion tracking via `[ ]` checkbox counting is active.
- **No silent failure**: Code review standards expect self-documenting code, minimal comments, small change radius, and net-negative line count for refactors. See `CONTRIBUTING.md` and `memory/code_review_principles.md`.
