#!/usr/bin/env python3
"""
Extract development-norm candidates from Claude Code chat logs.

Scans JSONL chat logs, filters noise, groups near-duplicate messages via
Jaccard clustering, and outputs a structured digest for LLM classification.
The script handles extraction / dedup / clustering only — semantic judgment
(signal type, priority, whether it's actually a norm) is left to the LLM.

Output:
    <project_dir>/.norm_digest.md            — structured norm analysis digest
    <project_dir>/.norm_extractor_state.json — incremental processing state
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Noise filtering
# ---------------------------------------------------------------------------

_XML_TAG = re.compile(r"<[^>]+>")
_ANSI_ESC = re.compile(r"\x1b\[[0-9;]*m")

_STOP_WORDS = {
    # ZH
    "继续",
    "好的",
    "嗯",
    "好",
    "是的",
    "对",
    "可以",
    "行",
    "明白",
    "知道了",
    "了解",
    "收到",
    "谢谢",
    "请继续",
    "开始吧",
    "来吧",
    "搞",
    "干",
    "做吧",
    "动手吧",
    "对。",
    "好。",
    "行。",
    # EN
    "ok",
    "okay",
    "hello",
    "hi",
    "hey",
    "thanks",
    "thx",
    "go on",
    "go ahead",
    "yes",
    "no",
    "yep",
    "nope",
    "sure",
    "got it",
    "nice",
    "cool",
    "great",
    "awesome",
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
    t = _ANSI_ESC.sub("", text)
    t = _XML_TAG.sub("", t)
    return t.strip()


def is_garbage(text: str) -> bool:
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
# Jaccard clustering — groups near-duplicate messages across sessions
# ---------------------------------------------------------------------------


def _word_set(text: str) -> set:
    """Tokenize into word set — CJK ranges included for mixed-language text."""
    t = re.sub(r"[^\w一-鿿㐀-䶿]", " ", text.lower())
    return {w for w in t.split() if len(w) > 1}


def jaccard_similarity(a: str, b: str) -> float:
    sa, sb = _word_set(a), _word_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def cluster_messages(messages: List[str], threshold: float = 0.45) -> List[List[str]]:
    """Group messages into clusters by Jaccard similarity (union-find).
    n is typically < 200, so O(n²) is acceptable.
    """
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

    for i in range(n):
        for j in range(i + 1, n):
            if jaccard_similarity(messages[i], messages[j]) >= threshold:
                union(i, j)

    clusters = defaultdict(list)
    for idx, m in enumerate(messages):
        clusters[find(idx)].append(m)

    return sorted(clusters.values(), key=len, reverse=True)


# ---------------------------------------------------------------------------
# JSONL extraction
# ---------------------------------------------------------------------------


def extract_from_file(filepath: Path) -> List[Tuple[str, str, str]]:
    """Extract user messages from a JSONL file.
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
# Digest output — clusters first (highest signal), then full-text listing
# ---------------------------------------------------------------------------


def write_digest(
    digest_file: Path,
    now: datetime,
    messages: List[Tuple[str, str, str]],
    cluster_threshold: float = 0.45,
):
    if not messages:
        with open(digest_file, "w") as f:
            f.write(f"# Chat Norms Digest — {now.strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write("_No new messages to analyze._\n")
        return

    session_ids = set(sid for _, sid, _ in messages)
    texts = [t for t, _, _ in messages]

    # Cross-session tracking
    text_to_sessions = defaultdict(set)
    for t, sid, _ in messages:
        text_to_sessions[t].add(sid)

    # Cluster
    clusters = cluster_messages(texts, threshold=cluster_threshold)

    # Sort singletons (clusters of size 1) by cross-session count
    singletons = [c[0] for c in clusters if len(c) == 1]
    singletons.sort(key=lambda t: -len(text_to_sessions[t]))

    with open(digest_file, "w") as f:
        f.write(f"# Chat Norms Digest — {now.strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(
            f"**{len(texts)}** unique messages from **{len(session_ids)}** sessions\n\n"
        )

        # --- Hot Topic Clusters (2+ messages) ---
        multi_clusters = [c for c in clusters if len(c) >= 2]
        if multi_clusters:
            f.write("## Hot Topic Clusters\n\n")
            f.write(
                "_These semantically-similar messages appeared across multiple places. "
                "The user likely emphasized the same concern repeatedly — "
                "high-priority norm candidates._\n\n"
            )
            for ci, cluster in enumerate(multi_clusters, 1):
                f.write(f"### Cluster {ci} ({len(cluster)} messages)\n\n")
                for m in cluster:
                    sessions = text_to_sessions[m]
                    ss = (
                        f" [across {len(sessions)} sessions]"
                        if len(sessions) > 1
                        else ""
                    )
                    f.write(f"- {_escape_md(m)}{ss}\n")
                f.write("\n")

        # --- Singletons, sorted by cross-session count ---
        if singletons:
            f.write("## All Messages\n\n")
            f.write(
                "_Single-occurrence messages, sorted by cross-session frequency._\n\n"
            )
            for t in singletons:
                sessions = text_to_sessions[t]
                ss = f" [across {len(sessions)} sessions]" if len(sessions) > 1 else ""
                f.write(f"- {_escape_md(t)}{ss}\n")
            f.write("\n")

        # --- Full text for LLM analysis ---
        f.write("---\n\n")
        f.write("## Full Message Text\n\n")
        f.write(
            "_Analyze each message below. Classify by signal type: CORRECTION, "
            "PRINCIPLE, FEEDBACK, PATTERN, REPETITION, YAGNI, TASK, QUESTION. "
            "Skip messages that are unrelated to coding norms. "
            "For each norm candidate, extract a concise rule and decide whether "
            "it belongs in global CLAUDE.md or project CLAUDE.md._\n\n"
        )
        for i, (t, _, _) in enumerate(messages, 1):
            sessions = text_to_sessions[t]
            ss = f" [across {len(sessions)} sessions]" if len(sessions) > 1 else ""
            f.write(f"**{i}.**{ss}\n\n{t}\n\n")


def _escape_md(text: str) -> str:
    return text.replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Extract development-norm candidates from Claude Code chat logs."
    )
    parser.add_argument(
        "project_dir",
        help="Project directory (chat logs expected at ~/.claude/projects/<slug>/)",
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
        help="Jaccard similarity threshold for clustering (0–1, default: 0.45)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=0,
        help="Only process conversations from the last N days (0 = unlimited; 7/14/30 for cron)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print progress to stderr",
    )
    args = parser.parse_args()

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=sys.stderr)
        sys.exit(1)

    slug = str(project).replace("/", "-")
    logs_dir = Path.home() / ".claude/projects" / slug
    state_file = project / ".norm_extractor_state.json"
    digest_file = project / ".norm_digest.md"

    state = load_state(state_file)
    last_ts = datetime.fromisoformat(state["last_processed"]).replace(
        tzinfo=timezone.utc
    )
    now = datetime.now(timezone.utc)

    days_cutoff = None
    if args.days > 0:
        days_cutoff = now - timedelta(days=args.days)

    new_files = []
    if logs_dir.exists():
        for f in sorted(logs_dir.glob("*.jsonl")):
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime > last_ts:
                if days_cutoff is not None and mtime < days_cutoff:
                    continue
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
