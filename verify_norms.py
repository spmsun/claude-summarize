#!/usr/bin/env python3
"""
Compliance verification — reads CLAUDE.md rules, generates an LLM review prompt.

No built-in check logic. Reads specs → scans project tree → outputs a single prompt.
The LLM reads the rules, understands them, scans project files, and writes the report.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

SKIP_DIRS = {
    ".git",
    "__pycache__",
    "venv",
    ".venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".claude",
    ".trae",
    "to_delete",
    "user_data",
}

PROMPT = """Review the CLAUDE.md coding standards below. Then scan the project files and check each rule for compliance violations. Produce a severity-graded report.

## Standards

{specs}

## Project Files

{files}

## Instructions

For each rule in the standards, search the project files for violations. Report by severity (HIGH / MEDIUM / LOW):

```
# Norm Compliance Report — {timestamp}

## HIGH Severity
- `file.py:42` — violation description (quote the specific rule)

## MEDIUM Severity
- ...

## LOW Severity
- ...
```

Severity guidelines:
- HIGH = explicitly forbidden behavior (swallowed exceptions, wrong column types, etc.)
- MEDIUM = naming / structural rule violations
- LOW = style suggestions not followed
- Only report actual violations found. If a rule is fully complied with, skip it.
- Sort by file path. Quote the specific rule text for each violation.
- Write output to `<project>/.norm_compliance.md`
"""


def main():
    parser = argparse.ArgumentParser(
        description="Generate a compliance review prompt from CLAUDE.md"
    )
    parser.add_argument(
        "project_dir",
        nargs="?",
        default=".",
        help="Project root directory (default: current)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Prompt output path (default: <project>/.norm_check_prompt.md)",
    )
    parser.add_argument("--stdout", action="store_true", help="Print prompt to stdout")
    args = parser.parse_args()

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=__import__("sys").stderr)
        raise SystemExit(1)

    # Collect standards
    spec_parts = []
    for label, path in [
        ("Global", Path.home() / ".claude" / "CLAUDE.md"),
        ("Project", project / "CLAUDE.md"),
    ]:
        if path.exists():
            spec_parts.append(
                f"### {label}\n\n{path.read_text(encoding='utf-8', errors='replace')}"
            )
    specs = "\n\n---\n\n".join(spec_parts) if spec_parts else "(no CLAUDE.md found)"

    # Scan project files
    file_lines = []
    for f in sorted(project.rglob("*")):
        if set(f.parts).intersection(SKIP_DIRS):
            continue
        if f.is_file() and not f.name.startswith("."):
            try:
                rel = str(f.relative_to(project))
                file_lines.append(f"- `{rel}`")
            except ValueError:
                pass

    files = "\n".join(file_lines[:500])

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    prompt = PROMPT.format(specs=specs, files=files, timestamp=now)

    output_path = (
        Path(args.output) if args.output else (project / ".norm_check_prompt.md")
    )
    output_path.write_text(prompt, encoding="utf-8")
    print(f"Prompt written: {output_path}", file=__import__("sys").stderr)

    if args.stdout:
        print(prompt)


if __name__ == "__main__":
    main()
