"""代码与测试抽取。

用 AST 读取示例工程的类、函数、方法与测试用例，记录它们所在的文件、行号、
签名与源码片段，供检索阶段定位「需要修改的代码」与「用于验证的测试」。
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from sddctx.models import CodeSymbol, TestCase

MAX_BODY_CHARS = 4000


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> str:
    try:
        segment = ast.get_source_segment(source, node)
    except Exception:  # pragma: no cover - 源码片段缺失时退回签名文本
        segment = None
    if segment:
        first_line = segment.splitlines()[0].strip()
        if first_line.endswith(":"):
            first_line = first_line[:-1].strip()
        return first_line[:240]
    return node.name


def _body(node: ast.AST, source: str) -> str:
    try:
        segment = ast.get_source_segment(source, node) or ""
    except Exception:  # pragma: no cover
        segment = ""
    return segment[:MAX_BODY_CHARS]


def extract_symbols(path: Path, project_dir: Path) -> list[CodeSymbol]:
    """抽取一个源码文件中的类、函数与方法。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    relative = path.relative_to(project_dir).as_posix()
    symbols: list[CodeSymbol] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(
                CodeSymbol(
                    symbol=node.name,
                    kind="class",
                    file=relative,
                    line=node.lineno,
                    signature=f"class {node.name}",
                    doc=ast.get_docstring(node) or "",
                    body=_body(node, source),
                )
            )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            owner = _owner_class(tree, node)
            name = f"{owner}.{node.name}" if owner else node.name
            symbols.append(
                CodeSymbol(
                    symbol=name,
                    kind="method" if owner else "function",
                    file=relative,
                    line=node.lineno,
                    signature=_signature(node, source),
                    doc=ast.get_docstring(node) or "",
                    body=_body(node, source),
                )
            )
    return symbols


def _owner_class(tree: ast.Module, node: ast.AST) -> str | None:
    for item in ast.walk(tree):
        if isinstance(item, ast.ClassDef) and any(child is node for child in item.body):
            return item.name
    return None


def extract_tests(path: Path, project_dir: Path, known_symbols: set[str]) -> list[TestCase]:
    """抽取测试文件中的用例，并记录它们直接引用的代码符号。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    relative = path.relative_to(project_dir).as_posix()
    tests: list[TestCase] = []

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.startswith("test_"):
            continue
        body = _body(node, source)
        targets = sorted(_referenced_symbols(node, known_symbols))
        tests.append(
            TestCase(
                name=node.name,
                file=relative,
                line=node.lineno,
                doc=ast.get_docstring(node) or "",
                body=body,
                targets=tuple(targets),
            )
        )
    return tests


def _referenced_symbols(node: ast.AST, known_symbols: set[str]) -> set[str]:
    referenced: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            referenced.add(child.id)
        elif isinstance(child, ast.Attribute):
            referenced.add(child.attr)
    bare = {name for name in referenced if name in known_symbols}
    qualified = {
        symbol.split(".", 1)[1]
        for symbol in known_symbols
        if "." in symbol and symbol.split(".", 1)[1] in referenced
    }
    return bare | qualified


def collect_source_files(project_dir: Path) -> list[Path]:
    """工程源码文件列表。"""
    return sorted((project_dir / "src").rglob("*.py"))


def collect_test_files(project_dir: Path) -> list[Path]:
    """工程测试文件列表。"""
    return sorted((project_dir / "tests").rglob("test_*.py"))


def symbol_fingerprint(symbol: CodeSymbol) -> str:
    """代码符号指纹。"""
    return _digest(f"{symbol.file}\x1f{symbol.symbol}\x1f{symbol.body}")
