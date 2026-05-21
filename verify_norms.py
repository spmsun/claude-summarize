#!/usr/bin/env python3
"""
合规检查 — 读取 CLAUDE.md 规范，生成 LLM 审查提示词。

不内置任何检查逻辑。读取规范 → 扫描项目结构 → 输出一句提示词，
LLM 自行阅读理解规范、逐文件审查、输出分级违规报告。
"""

import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))

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

PROMPT = """读取以下 CLAUDE.md 编码规范，然后扫描项目文件，逐条检查规范遵守情况，输出分级违规报告。

## 规范文件

{specs}

## 项目文件清单

{files}

## 执行要求

对每一条规范，在项目文件中查找违规情况。按严重程度（HIGH / MEDIUM / LOW）分级报告：

```
# Norm Compliance Report — {timestamp}

## HIGH Severity
- `file.py:42` — 违规描述（引用具体规范原文）

## MEDIUM Severity
- ...

## LOW Severity
- ...
```

规则：
- HIGH = 明确禁止的行为（吞异常、JSON字段类型错误等）
- MEDIUM = 命名/结构规范违反
- LOW = 风格建议未遵守
- 仅报告实际发现的违规，规范全部遵守则写"未发现违规"
- 按文件路径排序，每条引用规范原文
- 输出保存到 `<project>/.norm_compliance.md`
"""


def main():
    parser = argparse.ArgumentParser(description="生成合规审查提示词")
    parser.add_argument(
        "project_dir", nargs="?", default=".", help="项目根目录路径（默认: 当前目录）"
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="提示词输出路径（默认: <project>/.norm_check_prompt.md）",
    )
    parser.add_argument("--stdout", action="store_true", help="输出提示词到终端")
    args = parser.parse_args()

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        print(f"错误: 目录不存在: {project}", file=__import__("sys").stderr)
        raise SystemExit(1)

    # 收集规范
    spec_parts = []
    for label, path in [
        ("全局规范", Path.home() / ".claude" / "CLAUDE.md"),
        ("项目规范", project / "CLAUDE.md"),
    ]:
        if path.exists():
            spec_parts.append(
                f"### {label}\n\n{path.read_text(encoding='utf-8', errors='replace')}"
            )
    specs = "\n\n---\n\n".join(spec_parts) if spec_parts else "(未找到 CLAUDE.md)"

    # 扫描项目文件
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

    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST")
    prompt = PROMPT.format(specs=specs, files=files, timestamp=now)

    output_path = (
        Path(args.output) if args.output else (project / ".norm_check_prompt.md")
    )
    output_path.write_text(prompt, encoding="utf-8")
    print(f"提示词已写入: {output_path}", file=__import__("sys").stderr)

    if args.stdout:
        print(prompt)


if __name__ == "__main__":
    main()
