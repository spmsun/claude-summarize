---
name: claude-summarize
description: >
  Extract development norms from Claude Code chat history, compare against CLAUDE.md,
  auto-append new rules. Cross-session learning. Trigger keywords: "提炼规范",
  "更新 CLAUDE.md", "回顾聊天记录", "总结开发规范", "auto-update claude.md",
  "cross-session learning", "/norms", "规范总结", "claude-summarize".
---

Extract development norms from Claude Code chat logs and update CLAUDE.md.

## Triggers

"提炼规范", "更新 CLAUDE.md", "回顾聊天", "总结规范",
"cross-session learning", "/norms", "claude-summarize".

## Workflow

### Step 1: Extract

```
python3 {baseDir}/extract_chat_norms.py <project_dir>
```

Options:
- `--days 7` — only process conversations from last N days (0=unlimited; 7/14/30 for cron)
- `--cluster-threshold 0.45` — Jaccard similarity threshold (0–1, default 0.45)
- `--min-length 15` / `--max-length 2000` — message length bounds
- `-v` — verbose progress output

Output `<project>/.norm_digest.md`:
- **Hot Topic Clusters** — semantically similar messages grouped (Jaccard word-set clustering), with cross-session occurrence counts
- **All Messages** — remaining singletons sorted by cross-session frequency
- **Full Message Text** — complete text for LLM analysis

### Step 2: Analyze

Read `.norm_digest.md`. The script handles extraction / dedup / clustering only — **all semantic classification is your job as the LLM.**

**Hot Topic Clusters first** — these are the strongest signals. When multiple messages cluster together, the user likely emphasized the same concern repeatedly. Extract a single concise principle from each cluster.

**Then singletons** — messages that appear across multiple sessions are more likely to be norms than one-off remarks.

Classify each message:
- `CORRECTION` — user correcting behavior (highest priority)
- `PRINCIPLE` — user stating a rule or principle
- `FEEDBACK` — problem/error report (may indicate missing norm)
- `YAGNI` — over-engineering / keep-it-simple warning
- `REPETITION` — same issue keeps happening (norm exists but not followed)
- `TASK` / `QUESTION` — low signal, skip unless they embed an implicit principle

**Ignore messages unrelated to coding norms** (office politics, random chat, project-specific one-off tasks).

Cross-reference candidate norms against CLAUDE.md (global `~/.claude/CLAUDE.md` first, then project `<project>/CLAUDE.md`):
- Already present → skip
- Missing → proceed to Step 3
- Conflict → flag and ask user

### Step 3: Refine

Consolidate before writing:
- **Merge equivalents** — similar rules into one; keep only the most general
- **Cut redundancy** — one line per rule, no filler
- **Abstract to principles** — no file paths, filenames, or other perishable details

### Step 4: Update

- **Global / universal** (naming, DRY/KISS, error handling, etc.) → `~/.claude/CLAUDE.md`
- **Project-specific** (ORM, scraper architecture, table schemas, etc.) → `<project>/CLAUDE.md`

No new norms → output "No new norms this run."

## Rules

- Multi-part requirements ("scan all references", "sync everywhere") → single rule, never split
- Extraction script runs silently; only output a change summary when new norms are found
- Suggested cron: `/loop daily at 7:17 run claude-summarize`

## Sample Output

```
# Chat Norms Digest — 2026-05-20 07:04

**48** unique messages from **15** sessions

## Hot Topic Clusters

### Cluster 1 (3 messages)
- Config duplication keeps causing issues… [across 2 sessions]
- Multiple YAML files define the same retry settings…
- Why are there three retry configs that all overlap…

### Cluster 2 (2 messages)
- Don't use positional index to match source data… [across 2 sessions]
- batch_items[i] mapping is unreliable, skip and warn instead…

## All Messages

- Every time I say "scan all references" you must check… [across 2 sessions]
- 不要为了几行代码提炼函数，只用一两次的逻辑不要提炼函数
...

## Full Message Text

**1.** 不要为了几行代码提炼函数…
```
