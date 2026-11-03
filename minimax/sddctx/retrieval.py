"""上下文检索与组装。

输入一个开发任务，输出该任务适用的规格条目、需要修改的代码符号、用于验证的测试。
检索全部通过 seekdb / OceanBase 的 SQL 完成，取回的记录即为上下文内容。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sddctx.models import CodeSymbol, SpecRule, TestCase
from sddctx.store import SeekDBStore

SPEC_ID_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]*-[A-Z0-9]+-\d{2,4}\b")
LATIN_WORD_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CJK_PATTERN = re.compile(r"[一-鿿]{2,}")

MAX_TOKENS = 28
MIN_KEYWORD_SCORE = 2


def _mentions(text: str, symbol: str) -> bool:
    """任务描述是否直接点名了某个符号。"""
    lowered = text.lower()
    short = symbol.split(".", 1)[-1].lower()
    return short in lowered or symbol.lower() in lowered


def tokenize(text: str) -> list[str]:
    """任务描述切分为检索词：英文按单词，中文按二元组。"""
    lowered = text.lower()
    tokens: set[str] = set()

    for match in LATIN_WORD_PATTERN.finditer(lowered):
        word = match.group()
        if len(word) >= 2:
            tokens.add(word)

    for match in CJK_PATTERN.finditer(lowered):
        run = match.group()
        if len(run) <= 6:
            tokens.add(run)
        for index in range(len(run) - 1):
            tokens.add(run[index : index + 2])

    return sorted(tokens)[:MAX_TOKENS]


def spec_ids_in(text: str) -> list[str]:
    """任务描述中显式出现的规格编号。"""
    return list(dict.fromkeys(SPEC_ID_PATTERN.findall(text)))


def identifiers_in(texts: list[str]) -> list[str]:
    """从文本中取出可能的代码标识符。"""
    found: set[str] = set()
    for text in texts:
        for match in LATIN_WORD_PATTERN.finditer(text):
            word = match.group()
            if "_" in word or (word[:1].isupper() and len(word) > 2):
                found.add(word)
    return sorted(found)


@dataclass
class ContextBundle:
    """一次任务检索得到的上下文。"""

    task_id: str
    task: str
    generated_at: str
    specs: list[SpecRule] = field(default_factory=list)
    spec_scores: dict[str, int] = field(default_factory=dict)
    code: list[CodeSymbol] = field(default_factory=list)
    primary_code: list[CodeSymbol] = field(default_factory=list)
    code_scores: dict[str, int] = field(default_factory=dict)
    target_tests: list[TestCase] = field(default_factory=list)
    regression_tests: list[TestCase] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task": self.task,
            "generated_at": self.generated_at,
            "tokens": self.tokens,
            "specs": [
                {**rule.to_dict(), "score": self.spec_scores.get(f"{rule.spec_id}::{rule.rule_id}", 0)}
                for rule in self.specs
            ],
            "code": [
                {**symbol.to_dict(), "score": self.code_scores.get(symbol.qualified, 0)}
                for symbol in self.code
            ],
            "primary_code": [symbol.qualified for symbol in self.primary_code],
            "target_tests": [test.to_dict() for test in self.target_tests],
            "regression_tests": [test.to_dict() for test in self.regression_tests],
            "notes": self.notes,
        }

    def to_markdown(self) -> str:
        """给人阅读的上下文摘要。"""
        lines = [f"# 开发任务上下文 {self.task_id}", "", f"- 任务描述：{self.task}", ""]
        lines.extend(self._spec_lines())
        lines.append("")
        lines.extend(self._code_lines())
        lines.append("")
        lines.extend(self._target_test_lines())
        lines.append("")
        lines.extend(self._regression_test_lines())
        lines.append("")

        if self.notes:
            lines.append("## 检索说明")
            lines.extend(f"- {note}" for note in self.notes)
            lines.append("")

        return "\n".join(lines)

    def _spec_lines(self) -> list[str]:
        lines = ["## 适用规格"]
        if not self.specs:
            return [*lines, "- 未检索到适用规格"]

        for rule in self.specs:
            lines.append(
                f"- **{rule.rule_id} {rule.rule_title}**（来源：`{rule.source_file}`，"
                f"规格 `{rule.spec_id}` {rule.spec_title}）"
            )
            for body_line in rule.body.splitlines():
                if body_line.strip():
                    lines.append(f"  {body_line.strip()}")
        return lines

    def _code_lines(self) -> list[str]:
        lines = ["## 需要修改的代码"]
        if not self.primary_code:
            lines.append("- 未检索到相关代码")
        else:
            for symbol in self.primary_code:
                lines.append(f"- `{symbol.qualified}`（{symbol.kind}，第 {symbol.line} 行）")
                lines.append(f"  签名：`{symbol.signature}`")
                if symbol.doc:
                    lines.append(f"  说明：{symbol.doc.splitlines()[0]}")

        supporting = [symbol for symbol in self.code if symbol not in self.primary_code]
        if supporting:
            lines.append("")
            lines.append("### 相关但未直接修改的代码")
            for symbol in supporting:
                lines.append(f"- `{symbol.qualified}`（{symbol.kind}，第 {symbol.line} 行）")
        return lines

    def _target_test_lines(self) -> list[str]:
        lines = ["## 目标功能测试"]
        if not self.target_tests:
            return [*lines, "- 未检索到目标测试"]

        for test in self.target_tests:
            lines.append(f"- `{test.qualified}`（第 {test.line} 行）")
            if test.doc:
                lines.append(f"  {test.doc.splitlines()[0]}")
        return lines

    def _regression_test_lines(self) -> list[str]:
        lines = ["## 回归测试"]
        if not self.regression_tests:
            return [*lines, "- 无"]

        for test in self.regression_tests:
            lines.append(f"- `{test.qualified}`")
        return lines


def _collect_specs(
    store: SeekDBStore,
    *,
    task: str,
    tokens: list[str],
    spec_limit: int,
) -> tuple[list[SpecRule], dict[str, int], list[str]]:
    """检索适用规格条目。"""
    explicit_spec_ids = spec_ids_in(task)
    notes: list[str] = []
    spec_rules: dict[tuple[str, str], SpecRule] = {}
    spec_scores: dict[str, int] = {}

    if explicit_spec_ids:
        for rule, score in store.search_spec_rules([], spec_ids=explicit_spec_ids, limit=spec_limit * 2):
            spec_rules[(rule.spec_id, rule.rule_id)] = rule
            spec_scores[f"{rule.spec_id}::{rule.rule_id}"] = score
        notes.append(f"任务显式引用规格 {'、'.join(explicit_spec_ids)}，其全部规则纳入上下文。")

    for rule, score in store.search_spec_rules(tokens, limit=spec_limit):
        if score < MIN_KEYWORD_SCORE:
            continue
        key = (rule.spec_id, rule.rule_id)
        if key not in spec_rules:
            spec_rules[key] = rule
        spec_scores[f"{rule.spec_id}::{rule.rule_id}"] = max(
            spec_scores.get(f"{rule.spec_id}::{rule.rule_id}", 0), score
        )

    selected = sorted(spec_rules.values(), key=lambda rule: (rule.spec_id, rule.rule_id))
    return selected, spec_scores, notes


def _collect_code(
    store: SeekDBStore,
    *,
    task: str,
    tokens: list[str],
    specs: list[SpecRule],
    code_limit: int,
) -> tuple[list[CodeSymbol], dict[str, int], list[CodeSymbol]]:
    """检索需要修改的代码符号。"""
    candidate_names = identifiers_in([rule.body for rule in specs] + [task])
    code_hits = store.search_code_symbols(tokens, limit=code_limit, names=candidate_names)
    code_symbols: list[CodeSymbol] = []
    code_scores: dict[str, int] = {}
    for symbol, score in code_hits:
        if score <= 0:
            continue
        code_symbols.append(symbol)
        code_scores[symbol.qualified] = score

    primary = [symbol for symbol in code_symbols if _mentions(task, symbol.symbol)]
    if not primary:
        primary = list(code_symbols)
    return code_symbols, code_scores, primary


def _collect_tests(
    store: SeekDBStore,
    *,
    tokens: list[str],
    primary_names: set[str],
) -> tuple[list[TestCase], list[TestCase]]:
    """按主符号划分目标功能测试与回归测试。"""
    all_tests = store.list_test_cases()
    relevant = store.search_test_cases(tokens, limit=len(all_tests) or 1, target_symbols=sorted(primary_names))
    relevant_keys = {test.qualified for test, _ in relevant}

    target_tests = [test for test in all_tests if set(test.targets) & primary_names]
    target_tests.sort(key=lambda test: (test.file, test.line))
    target_keys = {test.qualified for test in target_tests}

    regression_tests = [
        test
        for test in all_tests
        if test.qualified not in target_keys and test.qualified in relevant_keys
    ]
    if not regression_tests:
        regression_tests = [test for test in all_tests if test.qualified not in target_keys]
    return target_tests, regression_tests


def build_context(
    store: SeekDBStore,
    *,
    task_id: str,
    task: str,
    spec_limit: int = 12,
    code_limit: int = 10,
) -> ContextBundle:
    """按任务检索上下文。"""
    tokens = tokenize(task)
    selected_specs, spec_scores, notes = _collect_specs(
        store, task=task, tokens=tokens, spec_limit=spec_limit
    )
    code_symbols, code_scores, primary_symbols = _collect_code(
        store, task=task, tokens=tokens, specs=selected_specs, code_limit=code_limit
    )
    primary_names = {name for symbol in primary_symbols for name in (symbol.symbol, symbol.symbol.split(".", 1)[-1])}
    target_tests, regression_tests = _collect_tests(store, tokens=tokens, primary_names=primary_names)

    if not code_symbols:
        notes.append("未检索到与任务匹配的代码符号，请检查任务描述或重新导入工程资料。")

    return ContextBundle(
        task_id=task_id,
        task=task,
        generated_at=datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        specs=selected_specs,
        spec_scores=spec_scores,
        code=code_symbols,
        primary_code=primary_symbols,
        code_scores=code_scores,
        target_tests=target_tests,
        regression_tests=regression_tests,
        tokens=tokens,
        notes=notes,
    )
