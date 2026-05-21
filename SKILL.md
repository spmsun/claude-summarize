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

## CLI Reference

```
python3 {baseDir}/extract_chat_norms.py <project_dir> [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--days N` | `0` (unlimited) | Only process last N days (7/14/30 for cron) |
| `--cluster-threshold T` | `0.45` | Jaccard or cosine similarity threshold (0–1) |
| `--cluster-engine E` | `jaccard` | `jaccard` (zero-deps) or `embedding` (cross-language, needs `sentence-transformers`) |
| `--auto` | off | Auto mode — digest includes proceed-without-pausing instruction |
| `--min-length N` | `15` | Minimum message length |
| `--max-length N` | `2000` | Maximum message length |
| `-v` | off | Verbose progress output |

## Workflow

### Step 1: Extract

```
python3 {baseDir}/extract_chat_norms.py <project_dir> [--days 7] [--auto] [--cluster-engine jaccard|embedding]
```

Output `<project>/.norm_digest.md`:
- **Hot Topic Clusters** — semantically similar messages grouped (Jaccard or embedding clustering), with cross-session occurrence counts
- **All Messages** — remaining singletons sorted by cross-session frequency
- **Full Message Text** — complete text for LLM analysis

**Clustering engines:**
- `jaccard` (default): Zero-dependency word-set overlap. Fast, works for same-language messages. Fails to cluster "别吞异常" with "don't swallow exceptions".
- `embedding`: Uses `all-MiniLM-L6-v2` via `sentence-transformers`. Cosine similarity on multilingual embeddings clusters cross-language messages correctly. Lazy-imported — no dependency unless actually used.

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

#### Rule Conflict Detection

**Before writing any new norm**, check it against ALL existing rules in both CLAUDE.md files:

1. **Direct contradiction**: New rule says "always do X", existing rule says "never do X" → **HALT, report conflict** with both rules quoted verbatim, do NOT write either
2. **Semantic overlap — narrower**: New rule is subset of existing → skip (existing covers it)
3. **Semantic overlap — broader**: New rule subsumes existing → replace existing with broader rule
4. **Tension**: Two rules pull in opposite directions (e.g., "inline everything" vs "DRY above all") → flag as tension, write the more specific one, add `> ⚠️ TENSION` comment above it in CLAUDE.md

**Conflict report format** (when unresolvable):
```
> ⚠️ RULE CONFLICT DETECTED — manual resolution needed
> New candidate: "<new rule>"
> Conflicts with (global|project): "<existing rule>"
> Reason: <one-line explanation of the contradiction>
```

#### Source Tracing

When a norm is accepted and written to CLAUDE.md, record its provenance in `<project>/.norm_sources.json`:

```json
{
  "norms": {
    "严禁吞噬异常": {
      "derived_from": ["2026-05-20 session: social_wechat 吞异常（20+ 处）"],
      "written_at": "2026-05-20T07:04:00",
      "type": "CORRECTION"
    }
  }
}
```

This keeps CLAUDE.md clean (no source bloat) while preserving traceability. LLM reads `.norm_sources.json` only when auditing norms, not on every session load.

### Step 3: Refine

Consolidate before writing:
- **Merge equivalents** — similar rules into one; keep only the most general
- **Cut redundancy** — one line per rule, no filler
- **Abstract to principles** — no file paths, filenames, or other perishable details

### Step 4: Update

- **Global / universal** (naming, DRY/KISS, error handling, etc.) → `~/.claude/CLAUDE.md`
- **Project-specific** (ORM, scraper architecture, table schemas, etc.) → `<project>/CLAUDE.md`

After writing, append source entries to `<project>/.norm_sources.json`.

No new norms → output "No new norms this run."

### Auto Mode

When digest header contains `> AUTO` (from `--auto` flag):

**Proceed through ALL steps without pausing for confirmation.** The digest includes explicit structured instructions. After analysis:
1. Classify every message
2. Run conflict detection on all candidates
3. Refine and merge equivalents
4. Write new norms directly to the appropriate CLAUDE.md
5. Update `.norm_sources.json` with provenance
6. Output a change summary: "Added N norms to global, M to project. K conflicts skipped."

Auto mode is designed for cron/loop invocation:
```
/loop daily at 7:17 run claude-summarize --auto --days 1
```

## Compliance Verification

```
python3 {baseDir}/verify_norms.py <project_dir> [--severity HIGH|MEDIUM|LOW|ALL] [-v]
```

Scans project code against CLAUDE.md rules and outputs `<project>/.norm_compliance.md`.

| Rule | Method | Severity |
|---|---|---|
| 严禁吞噬异常 | grep `except\s+.*:\s*pass` | HIGH |
| `_count` → `_cnt` | grep `_count\b` (skip `_cnt`) | MEDIUM |
| 中文标识用 `cn` 不用 `zh` | grep `"zh"` / `'zh'` in lang fields | LOW |
| 爬虫 `.py` + `.yaml` 配对 | glob check in `crawler/spiders/` | MEDIUM |
| JSON 字段用 Text | grep `Column.*JSON` | HIGH |
| 禁止二次包皮 | AST: 1-line body that only delegates | MEDIUM |
| 禁止同名目录嵌套 | check dir name duplication across depths | LOW |
| url 优先用 url_hash | find `url` columns without matching `url_hash` | LOW |
| 空 `__init__.py` | find `__init__.py` with only comments | LOW |

## Rules

- Multi-part requirements ("scan all references", "sync everywhere") → single rule, never split
- Extraction script runs silently; only output a change summary when new norms are found
- Before writing any norm, run conflict detection against BOTH CLAUDE.md files
- Source tracing goes to `.norm_sources.json`, NOT into CLAUDE.md
- Suggested cron: `/loop daily at 7:17 run claude-summarize --auto --days 1`

## Sample Output

```
# Chat Norms Digest — 2026-05-20 07:04

> AUTO

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
