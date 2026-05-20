#!/usr/bin/env python3
"""
Extract and analyze development norms from Claude Code chat logs.

Scans JSONL chat logs, filters noise, groups similar messages (clustering),
scores "norm signal strength", and outputs a structured digest for LLM analysis.

Usage:
    python3 extract_chat_norms.py <project_dir>
    python3 extract_chat_norms.py <project_dir> --min-length 20  --max-length 500

Output:
    <project_dir>/.norm_digest.md        — structured norm analysis digest
    <project_dir>/.norm_extractor_state.json  — incremental processing state

Features:
    - Near-duplicate clustering via Jaccard similarity (no external deps)
    - Signal strength scoring (correction / principle / feedback / task / question)
    - Cross-session tracking (how many sessions raised similar concerns)
    - Incremental processing (state file tracks what was already seen)
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Noise filtering
# ---------------------------------------------------------------------------

_XML_TAG = re.compile(r"<[^>]+>")
_ANSI_ESC = re.compile(r"\x1b\[[0-9;]*m")

_STOP_WORDS = {
    "继续",
    "好的",
    "嗯",
    "好",
    "ok",
    "hello",
    "hi",
    "hey",
    "是的",
    "对",
    "可以",
    "行",
    "明白",
    "知道了",
    "了解",
    "收到",
    "谢谢",
    "thanks",
    "thx",
    "请继续",
    "go on",
    "go ahead",
    "yes",
    "no",
    "对。",
    "好。",
    "行。",
    "开始吧",
    "来吧",
    "搞",
    "干",
    "做吧",
    "动手吧",
}

_SYSTEM_PREFIXES = (
    "Set model to",
    "Enabled plan mode",
    "This session is being continued from a previous conversation",
    "# Update Config Skill",
    "# Simplify",
    "simplify /simplify",
    "/simplify",
    "# ",
    "/plan",
    "plan plan",
    "Base directory for this skill:",
    "Stop hook feedback:",
    "[Image: source:",
    "A session-scoped Stop hook is now active",
)

_SYSTEM_TAG_FRAGMENTS = (
    "<task-notification>",
    "<task-id>",
    "<output-file>",
    "<local-command-caveat>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
)

_MIN_LENGTH = 15
_MAX_LENGTH = 2000


def strip_envelope(text: str) -> str:
    """Remove ANSI escapes and XML tags."""
    t = _ANSI_ESC.sub("", text)
    t = _XML_TAG.sub("", t)
    return t.strip()


def is_garbage(text: str) -> bool:
    """Return True if this text should be excluded from analysis."""
    t = " ".join(text.split()).strip()
    if not t:
        return True
    if re.match(r"^/\w+", t) and len(t) < 30:
        return True
    if any(kw in t.lower() for kw in ("interrupted by user", "do not respond")):
        return True
    if t.lower() in _STOP_WORDS:
        return True
    if t.startswith(_SYSTEM_PREFIXES):
        return True
    if "completed Agent" in t or "Monitor event:" in t:
        return True
    if t.startswith("The user named this session"):
        return True
    if re.match(r"^[a-f0-9]{12,} call_", t):
        return True
    # Empty local-command output indicator
    if re.match(r"^\(.+ completed with no output\)$", t):
        return True
    if len(t) < _MIN_LENGTH:
        return True
    if len(t) > _MAX_LENGTH:
        return True
    return False


def has_system_tag(text: str) -> bool:
    return any(tag in text for tag in _SYSTEM_TAG_FRAGMENTS)


# ---------------------------------------------------------------------------
# Classification / signal scoring
# ---------------------------------------------------------------------------

# Patterns that indicate different types of norm signals
# Each: (label, weight, keyword_list)
_SIGNAL_PATTERNS = [
    (
        "CORRECTION",
        3.0,
        [
            "不要",
            "不对",
            "错了",
            "应该是",
            "不是",
            "而非",
            "不应该",
            "别用",
            "别这么",
            "不能这样",
            "禁止",
        ],
    ),
    (
        "PRINCIPLE",
        2.5,
        [
            "必须",
            "记住",
            "以后",
            "规范是",
            "原则是",
            "一律",
            "统一",
            "不得",
            "须",
            "严禁",
            "不准",
            "只能",
        ],
    ),
    (
        "FEEDBACK",
        2.0,
        [
            "问题",
            "bug",
            "错误",
            "失败",
            "注意",
            "修复",
            "改一下",
            "重写",
            "优化",
            "漏洞",
        ],
    ),
    (
        "PATTERN",
        1.8,
        [
            "按规范",
            "按照",
            "模式",
            "套路",
            "惯例",
            "标准",
            "常规",
            "推荐",
            "建议",
        ],
    ),
    (
        "REPETITION",
        1.5,
        [
            "又",
            "再次",
            "还是",
            "仍然",
            "依然",
            "重复",
            "多次",
            "仍然不",
        ],
    ),
    (
        "YAGNI",
        1.5,
        [
            "过度",
            "多余",
            "没必要",
            "不需要",
            "不用",
            "精简",
            "简化",
            "过度设计",
            "嵌套",
        ],
    ),
    (
        "TASK",
        1.0,
        [
            "运行",
            "执行",
            "检查",
            "审查",
            "测试",
            "对比",
            "实现",
            "开发",
            "创建",
            "迁移",
        ],
    ),
    (
        "QUESTION",
        0.5,
        [
            "为什么",
            "什么",
            "怎么",
            "如何",
            "吗？",
            "？",
            "是不是",
            "能不能",
            "是否",
        ],
    ),
]


def classify_text(text: str) -> List[Tuple[str, float]]:
    """Return list of (signal_type, weight) for all matched patterns."""
    results = []
    low = text.lower()
    for label, weight, keywords in _SIGNAL_PATTERNS:
        for kw in keywords:
            if kw in text or kw in low:
                results.append((label, weight))
                break  # one match per category is enough
    if not results:
        results.append(("GENERIC", 0.3))
    return results


def compute_signal_score(text: str) -> float:
    """Aggregate signal score from all matched patterns."""
    tags = classify_text(text)
    # Boost for multiple signal types
    unique_types = len(set(t[0] for t in tags))
    score = sum(w for _, w in tags)
    if unique_types >= 3:
        score *= 1.3
    return round(score, 1)


def best_label(text: str) -> str:
    """Return the strongest applicable label for display."""
    tags = classify_text(text)
    if not tags:
        return "GENERIC"
    return max(tags, key=lambda x: x[1])[0]


# ---------------------------------------------------------------------------
# Near-duplicate detection (Jaccard word-set similarity)
# ---------------------------------------------------------------------------


def _word_set(text: str) -> set:
    """Tokenize into word set, dropping very short tokens."""
    t = re.sub(r"[^\w一-鿿]", " ", text.lower())
    return {w for w in t.split() if len(w) > 1}


def jaccard_similarity(a: str, b: str) -> float:
    """Jaccard similarity of word sets between two texts."""
    sa, sb = _word_set(a), _word_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def cluster_messages(messages: List[str], threshold: float = 0.45) -> List[List[str]]:
    """Group messages into clusters by word-set similarity (graph-based)."""
    n = len(messages)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Compare each pair (n is typically < 200, so O(n²) is fine)
    for i in range(n):
        for j in range(i + 1, n):
            if jaccard_similarity(messages[i], messages[j]) >= threshold:
                union(i, j)

    clusters = defaultdict(list)
    for idx, m in enumerate(messages):
        clusters[find(idx)].append(m)

    # Return clusters sorted by size (largest first)
    return sorted(clusters.values(), key=len, reverse=True)


# ---------------------------------------------------------------------------
# JSONL extraction
# ---------------------------------------------------------------------------


def extract_from_file(filepath: Path) -> List[Tuple[str, str, str]]:
    """
    Extract user messages from a JSONL file.

    Returns list of (text, session_id, git_branch).
    """
    entries = []
    try:
        with open(filepath) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "user":
                    continue
                msg = d.get("message", {})
                content = msg.get("content", "")
                if isinstance(content, list):
                    texts = [
                        c.get("text", "") for c in content if c.get("type") == "text"
                    ]
                    text = " ".join(texts)
                else:
                    text = str(content)

                if has_system_tag(text):
                    continue

                text = strip_envelope(text)
                if is_garbage(text):
                    continue

                cleaned = " ".join(text.split())
                session_id = d.get("sessionId", "?")
                git_branch = d.get("gitBranch", "")
                entries.append((cleaned, session_id, git_branch))
    except OSError as e:
        print(f"  [WARN] {filepath}: {e}", file=sys.stderr)
    return entries


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def load_state(state_file: Path) -> dict:
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {"last_processed": "2026-01-01T00:00:00"}


def save_state(state_file: Path, now: datetime):
    with open(state_file, "w") as f:
        json.dump({"last_processed": now.isoformat()}, f, indent=2)


# ---------------------------------------------------------------------------
# Digest output
# ---------------------------------------------------------------------------


def escape_md(text: str) -> str:
    """Escape markdown special chars in text for safe inclusion."""
    return text.replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")


def write_digest(
    digest_file: Path,
    now: datetime,
    messages: List[Tuple[str, str, str]],
    cluster_threshold: float = 0.45,
):
    """
    Write structured digest:
    - Summary stats
    - Hot topics (clusters with 2+ messages)
    - Per-message listing with signal tags and cross-session info
    """
    if not messages:
        with open(digest_file, "w") as f:
            f.write(f"# Chat Norms Digest — {now.strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write("_没有新的消息需要分析。_\n")
        return

    # Count sessions
    session_ids = set(sid for _, sid, _ in messages)
    texts = [t for t, _, _ in messages]

    # Build cross-session tracking
    text_to_sessions = defaultdict(set)
    for t, sid, _ in messages:
        text_to_sessions[t].add(sid)

    # Cluster
    clusters = cluster_messages(texts, threshold=cluster_threshold)

    # Score each message
    scored = [(compute_signal_score(t), t, text_to_sessions[t]) for t in texts]
    scored.sort(key=lambda x: -x[0])  # highest signal first

    with open(digest_file, "w") as f:
        f.write(f"# Chat Norms Digest — {now.strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(
            f"**{len(texts)}** unique messages from **{len(session_ids)}** sessions\n\n"
        )

        # --- Hot topic clusters ---
        multi_clusters = [c for c in clusters if len(c) >= 2]
        if multi_clusters:
            f.write("##  热点聚类\n\n")
            f.write(
                "_以下为语义相似的消息分组，同一组说明用户在多处表达了相似诉求。_\n\n"
            )
            for ci, cluster in enumerate(multi_clusters, 1):
                avg_score = sum(compute_signal_score(m) for m in cluster) / len(cluster)
                f.write(
                    f"### 聚类 {ci}（{len(cluster)} 条，平均信号强度 {avg_score:.1f}）\n\n"
                )
                for m in cluster:
                    sessions = text_to_sessions[m]
                    ss = f"横跨 {len(sessions)} 个会话" if len(sessions) > 1 else ""
                    f.write(f"- {escape_md(m)}{' — ' + ss if ss else ''}\n")
                f.write("\n")

        # --- Detailed listing (signal-ranked) ---
        f.write("##  消息明细（按信号强度排序）\n\n")
        f.write("| # | 信号 | 强度 | 消息 | 会话数 |\n")
        f.write("|---|------|------|------|--------|\n")
        for i, (score, t, sessions) in enumerate(scored, 1):
            label = best_label(t)
            sess_cnt = len(sessions)
            # Truncate very long messages in table
            display_t = t[:120] + "…" if len(t) > 120 else t
            display_t = escape_md(display_t)
            f.write(f"| {i} | {label} | {score} | {display_t} | {sess_cnt} |\n")

        # --- Full text listing for LLM consumption ---
        f.write("\n---\n\n")
        f.write("## 完整消息正文\n\n")
        for i, (score, t, sessions) in enumerate(scored, 1):
            label = best_label(t)
            sess_info = f" [跨 {len(sessions)} 会话]" if len(sessions) > 1 else ""
            f.write(f"**{i}.** `[{label}]` `[强度 {score}]`{sess_info} ")
            f.write(f"{t}\n\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Extract and analyze development norms from Claude Code chat logs."
    )
    parser.add_argument(
        "project_dir", help="Project directory (contains .claude/projects/<slug>/)"
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=15,
        help="Minimum message length to include (default: 15)",
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=2000,
        help="Maximum message length to include (default: 2000)",
    )
    parser.add_argument(
        "--cluster-threshold",
        type=float,
        default=0.45,
        help="Jaccard similarity threshold for clustering (0-1, default: 0.45)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Print progress to stderr"
    )
    args = parser.parse_args()

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=sys.stderr)
        sys.exit(1)

    # Derive logs dir from project path: $HOME/.claude/projects/<slug>
    slug = str(project).replace("/", "-")
    logs_dir = Path.home() / ".claude/projects" / slug
    state_file = project / ".norm_extractor_state.json"
    digest_file = project / ".norm_digest.md"

    state = load_state(state_file)
    last_ts = datetime.fromisoformat(state["last_processed"]).replace(
        tzinfo=timezone.utc
    )
    now = datetime.now(timezone.utc)

    new_files = []
    if logs_dir.exists():
        for f in sorted(logs_dir.glob("*.jsonl")):
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime > last_ts:
                new_files.append(f)

    if not new_files:
        if args.verbose:
            print("No new chat logs to process.", file=sys.stderr)
        save_state(state_file, now)
        return

    if args.verbose:
        print(f"Processing {len(new_files)} new chat log files...", file=sys.stderr)

    all_msgs = []
    for fpath in new_files:
        entries = extract_from_file(fpath)
        if entries:
            all_msgs.extend(entries)

    # Deduplicate by exact text (keep first occurrence's session/branch info)
    seen_texts = set()
    unique_msgs = []
    for text, sid, branch in all_msgs:
        if text in seen_texts:
            continue
        seen_texts.add(text)
        unique_msgs.append((text, sid, branch))

    write_digest(
        digest_file,
        now,
        unique_msgs,
        cluster_threshold=args.cluster_threshold,
    )
    save_state(state_file, now)

    if args.verbose:
        print(f"Digest written: {digest_file}", file=sys.stderr)
        print(
            f"{len(unique_msgs)} messages from {len(new_files)} files", file=sys.stderr
        )


if __name__ == "__main__":
    main()
