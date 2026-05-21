# Claude Summarize

Automatically extract development norms from Claude Code chat history, deduplicate against existing CLAUDE.md, and append new rules — cross-session learning for your AI coding assistant.

## How It Works

As developers converse with Claude Code, they naturally express coding conventions: corrections, principles, feedback, and repeated requests. This tool extracts those signals from JSONL chat logs, clusters semantically similar messages, scores their "norm potential," and produces a structured digest for LLM analysis. The LLM then compares findings against existing CLAUDE.md files and appends new norms.

## Features

- **Signal Scoring** — classifies each message as CORRECTION / PRINCIPLE / FEEDBACK / YAGNI / REPETITION / TASK / QUESTION with a weighted score
- **Semantic Clustering** — groups near-duplicate messages via Jaccard word-set similarity (zero external dependencies)
- **Cross-Session Tracking** — flags concerns that appear across multiple chat sessions
- **Incremental Processing** — state file tracks already-processed sessions; only new logs are analyzed
- **Time Window Filter** — `--days` flag limits analysis to recent conversations, ideal for cron jobs

## Quick Start

### Prerequisites

- Claude Code environment (chat logs at `~/.claude/projects/`)
- Python 3.8+

### Usage

```bash
# Extract norms from a project's chat history
python3 extract_chat_norms.py /path/to/your/project

# Limit to last 7 days of conversations (recommended for cron)
python3 extract_chat_norms.py /path/to/your/project --days 7

# Adjust clustering sensitivity
python3 extract_chat_norms.py /path/to/your/project --cluster-threshold 0.5

# Verbose output
python3 extract_chat_norms.py /path/to/your/project -v
```

Output files:
- `.norm_digest.md` — structured digest (hot topic clusters + signal score table + full message text)
- `.norm_extractor_state.json` — incremental processing state

### Full Workflow (as a Claude Code Skill)

1. Register this repo as a Skill in Claude Code
2. Run `/norms` or say "提炼规范" in your project
3. The LLM analyzes `.norm_digest.md`, compares against CLAUDE.md, and appends new rules

## Sample Digest Output

```
# Chat Norms Digest — 2026-05-20 07:04

**48** unique messages from **15** sessions

## 🔥 Hot Topic Clusters

### Cluster 1 (3 messages, avg signal 2.8)
- Config duplication keeps causing issues… [across 2 sessions]
- Multiple YAML files define the same retry settings…

## 📋 Messages by Signal Strength
| # | Signal | Score | Message | Sessions |
|---|--------|-------|---------|----------|
| 1 | CORRECTION | 3.0 | Don't use positional index to match source data… | 2 |
```

## Options

| Parameter | Default | Description |
|-----------|---------|-------------|
| `project_dir` | — | Project directory path |
| `--days` | 0 | Only process conversations from last N days (0=unlimited; 7/14/30 recommended for cron) |
| `--cluster-threshold` | 0.45 | Jaccard similarity threshold for clustering (0–1) |
| `--min-length` | 15 | Minimum message length to include |
| `--max-length` | 2000 | Maximum message length to include |
| `-v` | — | Verbose output to stderr |

## Architecture

```
Chat Logs (JSONL)         .norm_digest.md           CLAUDE.md
  │                           │                        ▲
  ▼                           ▼                        │
[extract script] ──▶ cluster + score ──▶ [LLM analysis] ──▶ deduplicate + write
                    zero-dependency        semantic check        global / project
                    incremental tracking                         no redundancy
```

## License

MIT
