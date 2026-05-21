#!/usr/bin/env python3
"""
合规检查脚本 — 根据 CLAUDE.md 规则检查项目代码合规性。

读取 ~/.claude/CLAUDE.md（全局）和 <project>/CLAUDE.md（项目），
扫描项目中的 Python 文件和 YAML 配置，输出合规报告到
<project>/.norm_compliance.md。

纯标准库实现，无外部依赖。
"""

import argparse
import ast
import fnmatch
import os
import re
import sys
import textwrap
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CST = timezone(timedelta(hours=8))

SKIP_PATTERNS = [
    ".git",
    "__pycache__",
    "venv",
    ".venv",
    "node_modules",
    "to_delete",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "*.egg-info",
    ".claude",  # worktrees 等内部目录
    ".trae",  # IDE 内部目录
    "user_data",  # 浏览器缓存 / 用户数据
]

SEVERITY_ORDER = ["HIGH", "MEDIUM", "LOW"]

PYTHON_BUILTINS = frozenset(
    {
        "abs",
        "all",
        "any",
        "bin",
        "bool",
        "bytes",
        "callable",
        "chr",
        "classmethod",
        "compile",
        "complex",
        "delattr",
        "dict",
        "dir",
        "divmod",
        "enumerate",
        "eval",
        "exec",
        "filter",
        "float",
        "format",
        "frozenset",
        "getattr",
        "globals",
        "hasattr",
        "hash",
        "hex",
        "id",
        "input",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "locals",
        "map",
        "max",
        "memoryview",
        "min",
        "next",
        "object",
        "oct",
        "open",
        "ord",
        "pow",
        "print",
        "property",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "setattr",
        "slice",
        "sorted",
        "staticmethod",
        "str",
        "sum",
        "super",
        "tuple",
        "type",
        "vars",
        "zip",
    }
)


@dataclass
class Violation:
    """一条违规记录"""

    file: str  # 相对于项目根目录的路径
    line: int  # 行号，0 表示不适用
    rule: str  # 规则名
    detail: str  # 违规详情
    severity: str  # HIGH / MEDIUM / LOW

    @staticmethod
    def mk(file, line, rule, detail, severity):  # noqa: D417
        return Violation(file, line, rule, detail, severity)


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def _should_skip(path: Path) -> bool:
    """路径的任一祖先匹配 SKIP_PATTERNS 则跳过"""
    for part in path.parts:
        for pat in SKIP_PATTERNS:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def _rel(path: Path, base: Path) -> str:
    """返回相对路径字符串"""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _read_file(path: Path) -> str | None:
    """读取文件内容，失败返回 None"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 规则检查函数（每个返回 list[Violation]）
# ---------------------------------------------------------------------------


def check_noexcept_pass(project_dir: Path, verbose: bool) -> list[Violation]:
    """检查 except: pass / except Exception: pass 吞噬异常"""
    violations: list[Violation] = []
    pattern = re.compile(
        r"except\b[^:]*:\s*pass\s*$|except\b[^:]*:\s*\n\s*pass\b",
        re.MULTILINE,
    )
    for py_file in project_dir.rglob("*.py"):
        if _should_skip(py_file):
            continue
        content = _read_file(py_file)
        if content is None:
            continue
        for m in pattern.finditer(content):
            line_no = content[: m.start()].count("\n") + 1
            matched = m.group(0)
            # 若匹配跨行，展示完整模式（except + pass）
            if "\n" in matched:
                detail_text = " ".join(
                    part.strip() for part in matched.split("\n") if part.strip()
                )
            else:
                detail_text = matched.strip()
            violations.append(
                Violation(
                    file=_rel(py_file, project_dir),
                    line=line_no,
                    rule="严禁吞噬异常",
                    detail=detail_text,
                    severity="HIGH",
                )
            )
    return violations


def check_json_column(project_dir: Path, verbose: bool) -> list[Violation]:
    """检查 Column 定义中使用 JSON 类型，应用 Text 替代"""
    violations: list[Violation] = []
    pattern = re.compile(r"Column\([^)]*\bJSON\b")
    for py_file in project_dir.rglob("*.py"):
        if _should_skip(py_file):
            continue
        content = _read_file(py_file)
        if content is None:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            if pattern.search(line):
                violations.append(
                    Violation(
                        file=_rel(py_file, project_dir),
                        line=i,
                        rule="JSON字段应用Text替代",
                        detail=line.strip(),
                        severity="HIGH",
                    )
                )
    return violations


def check_count_suffix(project_dir: Path, verbose: bool) -> list[Violation]:
    """检查 _count 结尾字段名，规范要求 _cnt（仅检查命名实体，不检查局部变量）"""
    violations: list[Violation] = []
    # 命名实体模式：dict 键、字符串中的字段引用、ORM Column 定义
    named_pat = re.compile(r"""["'](\w+_count)["']""")
    col_pat = re.compile(r"(\w+_count)\s*=\s*Column\b")
    # 排除的局部变量/函数调用模式
    local_pat = re.compile(
        r"^\s*\w+_count\s*=\s*\d|"  # local = 0
        r"\w+_count\s*[+\-*/%]?=|"  # augmented assignment
        r"\.\w*_count\s*\(|"  # method call
        r"\bos\.cpu_count\b"  # stdlib
    )

    for py_file in project_dir.rglob("*.py"):
        if _should_skip(py_file):
            continue
        if "test" in py_file.name.lower() or "/tests/" in str(py_file):
            continue
        content = _read_file(py_file)
        if content is None:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # 跳过明确的局部变量行
            if local_pat.search(line):
                continue
            # 检查命名实体模式
            matched = False
            for pat in (named_pat, col_pat):
                for m in pat.finditer(line):
                    word = m.group(1)
                    if word == "_count":
                        continue
                    violations.append(
                        Violation(
                            file=_rel(py_file, project_dir),
                            line=i,
                            rule="_count结尾字段统一为_cnt",
                            detail=line.strip(),
                            severity="MEDIUM",
                        )
                    )
                    matched = True
                    break
                if matched:
                    break
    # 同时检查 YAML 配置文件
    yaml_key_pat = re.compile(r"^\s*(\w+_count)\s*:")
    for yaml_file in list(project_dir.rglob("*.yaml")) + list(
        project_dir.rglob("*.yml")
    ):
        if _should_skip(yaml_file):
            continue
        content = _read_file(yaml_file)
        if content is None:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            m = yaml_key_pat.match(line)
            if m:
                word = m.group(1)
                if word == "_count":
                    continue
                violations.append(
                    Violation(
                        file=_rel(yaml_file, project_dir),
                        line=i,
                        rule="_count结尾字段统一为_cnt",
                        detail=line.strip(),
                        severity="MEDIUM",
                    )
                )
    return violations


def check_spider_pair(project_dir: Path, verbose: bool) -> list[Violation]:
    """检查爬虫 .py 是否有对应的 .yaml 配置文件"""
    violations: list[Violation] = []
    for candidate in ["crawler/spiders", "spiders"]:
        spiders_dir = project_dir / candidate
        if spiders_dir.is_dir():
            break
    else:
        return violations  # 无爬虫目录

    for py_file in spiders_dir.rglob("*.py"):
        if py_file.name.startswith("__"):
            continue
        yaml_path = py_file.with_suffix(".yaml")
        if not yaml_path.exists():
            violations.append(
                Violation(
                    file=_rel(py_file, project_dir),
                    line=0,
                    rule="爬虫.py/.yaml配对",
                    severity="MEDIUM",
                    detail=f"缺少对应的 YAML 配置: {yaml_path.name}",
                )
            )
    return violations


def check_wrapper_func(project_dir: Path, verbose: bool) -> list[Violation]:
    """检查二次包皮——函数体仅一行委托调用，零额外逻辑"""
    violations: list[Violation] = []
    for py_file in project_dir.rglob("*.py"):
        if _should_skip(py_file):
            continue
        content = _read_file(py_file)
        if content is None:
            continue
        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            # 跳过 dunder 方法和被装饰的函数
            if node.name.startswith("__") and node.name.endswith("__"):
                continue
            if node.decorator_list:
                continue

            body = _strip_docstring(node.body)
            if len(body) != 1:
                continue

            stmt = body[0]

            # 模式 1: return other_func(...)
            if isinstance(stmt, ast.Return) and stmt.value:
                call_expr = stmt.value
            # 模式 2: other_func(...) (无 return)
            elif isinstance(stmt, ast.Expr):
                call_expr = stmt.value
            else:
                continue

            if not isinstance(call_expr, ast.Call):
                continue

            # 仅标记裸函数名调用（不标记 self.method / obj.method 以降低误报）
            if not isinstance(call_expr.func, ast.Name):
                continue
            called = call_expr.func.id
            if called == node.name:
                continue  # 递归
            # 跳过 Python 内置函数（int/max/len 等作为表达式组件不是真委托）
            if called in PYTHON_BUILTINS:
                continue

            violations.append(
                Violation(
                    file=_rel(py_file, project_dir),
                    line=node.lineno,
                    rule="禁止二次包皮",
                    severity="MEDIUM",
                    detail=f"函数 '{node.name}' 仅一行委托调用 → {called}(), 无额外逻辑",
                )
            )
    return violations


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """移除函数体开头的文档字符串"""
    if not body:
        return body
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return body[1:]
    return body


def check_cn_not_zh(project_dir: Path, verbose: bool) -> list[Violation]:
    """检查使用 'zh' 而非 'cn' 作为中文标识"""
    violations: list[Violation] = []
    pattern = re.compile(r"""["']zh["']""")
    exclude_kw = re.compile(
        r"pytz|zhihu|zh_cn|zhong|zhao|zhen|zh_CN|zh_TW|zh_",
        re.IGNORECASE,
    )
    for py_file in project_dir.rglob("*.py"):
        if _should_skip(py_file):
            continue
        content = _read_file(py_file)
        if content is None:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            if not pattern.search(line):
                continue
            if exclude_kw.search(line):
                continue
            violations.append(
                Violation(
                    file=_rel(py_file, project_dir),
                    line=i,
                    rule="中文标识用cn不用zh",
                    detail=line.strip(),
                    severity="LOW",
                )
            )
    return violations


def check_url_hash(project_dir: Path, verbose: bool) -> list[Violation]:
    """检查 ORM 模型中 url 字段是否有对应的 url_hash"""
    violations: list[Violation] = []
    url_col = re.compile(r"""\bur[l]?\s*=\s*Column\b""", re.IGNORECASE)

    for py_file in project_dir.rglob("*.py"):
        if _should_skip(py_file):
            continue
        content = _read_file(py_file)
        if content is None:
            continue
        if not url_col.search(content):
            continue  # 无 url 字段，跳过 AST 解析

        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            fields: set[str] = set()
            url_fields: list[str] = []

            for item in node.body:
                if not isinstance(item, ast.Assign):
                    continue
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        fields.add(name)
                        if name.endswith("url") or name.endswith("_url"):
                            url_fields.append(name)

            for uf in url_fields:
                hash_name = f"{uf}_hash"
                if hash_name not in fields:
                    violations.append(
                        Violation(
                            file=_rel(py_file, project_dir),
                            line=node.lineno,
                            rule="url字段优先用url_hash",
                            severity="LOW",
                            detail=f"类 '{node.name}' 中字段 '{uf}' 缺少对应的 '{hash_name}'",
                        )
                    )
    return violations


def check_duplicate_dirs(project_dir: Path, verbose: bool) -> list[Violation]:
    """检查不同层级存在同名目录"""
    violations: list[Violation] = []
    dir_map: dict[str, list[str]] = defaultdict(list)

    for dirpath, dirnames, _ in os.walk(project_dir):
        dir_p = Path(dirpath)

        # 剪枝：移除应跳过的目录，防止 os.walk 深入
        prune: list[str] = []
        for d in dirnames:
            if _should_skip(dir_p / d):
                prune.append(d)
        for d in prune:
            dirnames.remove(d)

        if dir_p == project_dir:
            continue  # 跳过项目根

        name = dir_p.name
        rel = _rel(dir_p, project_dir)
        dir_map[name].append(rel)

    for name, paths in dir_map.items():
        if len(paths) <= 1:
            continue
        depths = {p.count(os.sep) for p in paths}
        if len(depths) <= 1:
            continue
        violations.append(
            Violation(
                file=paths[0],
                line=0,
                rule="不同层级不可有同名目录",
                severity="LOW",
                detail=f"目录 '{name}' 在多个层级出现: {', '.join(paths)}",
            )
        )
    return violations


def check_empty_init(project_dir: Path, verbose: bool) -> list[Violation]:
    """检查 __init__.py 仅含注释——无实际导出应清空"""
    violations: list[Violation] = []
    for py_file in project_dir.rglob("__init__.py"):
        if _should_skip(py_file):
            continue
        content = _read_file(py_file)
        if content is None:
            continue
        stripped = content.strip()
        if not stripped:
            continue  # 已空，合规

        # 剔除空行和注释行后无剩余内容 → 仅含注释
        meaningful = [
            l
            for l in stripped.splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]
        if not meaningful:
            violations.append(
                Violation(
                    file=_rel(py_file, project_dir),
                    line=0,
                    rule="空__init__.py仅含注释",
                    severity="LOW",
                    detail="__init__.py 仅含注释行，无实际导出，应清空",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# 规则注册表
# ---------------------------------------------------------------------------


@dataclass
class CheckEntry:
    name: str
    severity: str
    description: str
    fn: Callable[[Path, bool], list[Violation]]


CHECKS: list[CheckEntry] = [
    CheckEntry(
        "严禁吞噬异常",
        "HIGH",
        "except: pass / except Exception: pass 吞噬异常",
        check_noexcept_pass,
    ),
    CheckEntry(
        "JSON字段应用Text替代",
        "HIGH",
        "Column() 定义中使用了 JSON 类型，应使用 Text",
        check_json_column,
    ),
    CheckEntry(
        "_count结尾字段统一为_cnt",
        "MEDIUM",
        "字段名以 _count 结尾，规范要求 _cnt",
        check_count_suffix,
    ),
    CheckEntry(
        "爬虫.py/.yaml配对", "MEDIUM", "一个爬虫对应一对 .py + .yaml", check_spider_pair
    ),
    CheckEntry(
        "禁止二次包皮", "MEDIUM", "函数体仅一行委托调用，应直接内联", check_wrapper_func
    ),
    CheckEntry(
        "中文标识用cn不用zh",
        "LOW",
        "代码中使用 'zh' 作为中文标识，应用 'cn'",
        check_cn_not_zh,
    ),
    CheckEntry(
        "url字段优先用url_hash",
        "LOW",
        "ORM 模型中 url 字段缺少对应的 url_hash",
        check_url_hash,
    ),
    CheckEntry(
        "不同层级不可有同名目录",
        "LOW",
        "不同层级存在同名目录，应就近合并",
        check_duplicate_dirs,
    ),
    CheckEntry(
        "空__init__.py仅含注释",
        "LOW",
        "__init__.py 仅含注释行，无实际导出，应清空",
        check_empty_init,
    ),
]


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------


def generate_report(
    project_dir: Path,
    severities: set[str],
    verbose: bool,
) -> str:
    """运行所有检查并生成 Markdown 合规报告"""
    all_violations: list[Violation] = []
    rules_checked = 0
    failed_rules: list[str] = []

    for entry in CHECKS:
        if entry.severity not in severities:
            continue
        rules_checked += 1
        if verbose:
            print(f"  检查: {entry.name} ({entry.severity})...", file=sys.stderr)
        try:
            result = entry.fn(project_dir, verbose)
        except Exception as e:
            failed_rules.append(f"{entry.name}: {e}")
            if verbose:
                print(f"    ✗ 规则执行失败: {e}", file=sys.stderr)
            continue
        all_violations.extend(result)
        if verbose and result:
            print(f"    发现 {len(result)} 条违规", file=sys.stderr)

    # 按严重程度分组
    by_severity: dict[str, list[Violation]] = defaultdict(list)
    for v in all_violations:
        by_severity[v.severity].append(v)

    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST")
    global_md = Path.home() / ".claude" / "CLAUDE.md"
    project_md = project_dir / "CLAUDE.md"

    lines: list[str] = []
    lines.append("# Norm Compliance Report")
    lines.append("")
    lines.append(f"**Generated:** {now}")
    lines.append(f"**Project:** `{project_dir}`")
    lines.append(
        f"**Specs:** global={_status_icon(global_md.exists())} "
        f"project={_status_icon(project_md.exists())}"
    )
    lines.append(f"**Rules checked:** {rules_checked}")
    lines.append(f"**Violations found:** {len(all_violations)}")
    if failed_rules:
        lines.append(f"**Failed rules:** {len(failed_rules)}")
    lines.append("")

    for sev in SEVERITY_ORDER:
        items = by_severity.get(sev, [])
        if not items:
            continue
        lines.append(f"## {sev} Severity")
        lines.append("")
        for v in sorted(items, key=lambda x: (x.file, x.line)):
            loc = f"`{v.file}`"
            if v.line > 0:
                loc += f":{v.line}"
            lines.append(f"- {loc} — {v.detail}")
        lines.append("")

    if not all_violations:
        lines.append("## All Clear")
        lines.append("")
        lines.append("未发现违规项。")
        lines.append("")

    if failed_rules:
        lines.append("## Execution Warnings")
        lines.append("")
        for fr in failed_rules:
            lines.append(f"- {fr}")
        lines.append("")

    return "\n".join(lines)


def _status_icon(exists: bool) -> str:
    return "found" if exists else "missing"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="合规检查脚本 — 根据 CLAUDE.md 检查项目代码合规性",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        示例:
          %(prog)s                           # 检查当前目录
          %(prog)s /path/to/project          # 检查指定项目
          %(prog)s -s HIGH                   # 仅检查 HIGH 严重度
          %(prog)s -s HIGH,MEDIUM            # 检查 HIGH 和 MEDIUM
          %(prog)s -v                        # 详细输出
          %(prog)s --stdout                  # 同时输出报告到终端
        """),
    )
    parser.add_argument(
        "project_dir",
        nargs="?",
        default=".",
        help="项目根目录路径（默认: 当前目录）",
    )
    parser.add_argument(
        "--severity",
        "-s",
        default="ALL",
        help="严重度过滤: HIGH, MEDIUM, LOW, ALL（默认: ALL）",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="显示详细检查过程",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="报告输出路径（默认: <project>/.norm_compliance.md）",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="同时输出报告到 stdout",
    )

    args = parser.parse_args()

    project_dir = Path(args.project_dir).resolve()
    if not project_dir.is_dir():
        print(f"错误: 目录不存在: {project_dir}", file=sys.stderr)
        sys.exit(1)

    # 解析严重度过滤
    sev_raw = args.severity.upper()
    if sev_raw == "ALL":
        severities = {"HIGH", "MEDIUM", "LOW"}
    else:
        parts = {s.strip() for s in sev_raw.split(",")}
        invalid = parts - {"HIGH", "MEDIUM", "LOW"}
        if invalid:
            print(
                f"错误: 无效的严重度: {invalid}. " f"有效值: HIGH, MEDIUM, LOW, ALL",
                file=sys.stderr,
            )
            sys.exit(1)
        severities = parts

    if args.verbose:
        gm = Path.home() / ".claude" / "CLAUDE.md"
        pm = project_dir / "CLAUDE.md"
        print(
            f"全局规范: {gm} {'(存在)' if gm.exists() else '(不存在)'}", file=sys.stderr
        )
        print(
            f"项目规范: {pm} {'(存在)' if pm.exists() else '(不存在)'}", file=sys.stderr
        )
        print(f"严重度过滤: {severities}", file=sys.stderr)
        print("开始检查...", file=sys.stderr)

    report = generate_report(project_dir, severities, args.verbose)

    output_path = (
        Path(args.output) if args.output else (project_dir / ".norm_compliance.md")
    )
    output_path.write_text(report, encoding="utf-8")
    print(f"报告已写入: {output_path}", file=sys.stderr)

    if args.stdout:
        print(report)


if __name__ == "__main__":
    main()
