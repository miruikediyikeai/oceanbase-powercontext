"""规格文档解析。

规格是 Markdown 文件，结构约定：

- 首个一级标题给出规格标题；
- 标题之后的 `- key: value` 行给出元信息（id / status / tags 等）；
- 每个二级标题 `## <规则编号> <规则标题>` 是一条规则，标题下的正文是规则内容。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

RULE_HEADING = re.compile(r"^##\s+(?P<rule_id>[A-Za-z][A-Za-z0-9_\-]*)\s+(?P<title>.+?)\s*$")
META_LINE = re.compile(r"^-\s+(?P<key>[a-z_]+)\s*:\s*(?P<value>.*?)\s*$")
DOC_HEADING = re.compile(r"^#\s+(?P<title>.+?)\s*$")


@dataclass(frozen=True)
class SpecDocument:
    """一份规格文档及其规则。"""

    spec_id: str
    title: str
    status: str
    tags: tuple[str, ...]
    source_file: str
    rules: tuple[tuple[str, str, str], ...]

    def to_rules(self) -> list[SpecRuleView]:
        return [
            SpecRuleView(
                spec_id=self.spec_id,
                spec_title=self.title,
                spec_status=self.status,
                rule_id=rule_id,
                rule_title=rule_title,
                body=body,
                tags=self.tags,
                source_file=self.source_file,
                content_hash=rule_hash(self.spec_id, rule_id, rule_title, body),
            )
            for rule_id, rule_title, body in self.rules
        ]


@dataclass(frozen=True)
class SpecRuleView:
    """解析后的规格规则视图，落库时使用。"""

    spec_id: str
    spec_title: str
    spec_status: str
    rule_id: str
    rule_title: str
    body: str
    tags: tuple[str, ...]
    source_file: str
    content_hash: str


def rule_hash(spec_id: str, rule_id: str, rule_title: str, body: str) -> str:
    """规则内容指纹，用于识别规格是否被更新。"""
    digest = hashlib.sha256()
    digest.update("\x1f".join((spec_id, rule_id, rule_title, body)).encode("utf-8"))
    return digest.hexdigest()


def parse_spec_document(path: Path, source_file: str) -> SpecDocument:
    """解析单个规格文件。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    title = path.stem
    meta: dict[str, str] = {}
    rules: list[tuple[str, str, str]] = []

    current_rule: str | None = None
    current_title = ""
    buffer: list[str] = []

    def flush() -> None:
        if current_rule is not None:
            rules.append((current_rule, current_title, "\n".join(buffer).strip()))

    for line in lines:
        doc_match = DOC_HEADING.match(line)
        if doc_match and current_rule is None and title == path.stem:
            title = doc_match.group("title")
            continue

        rule_match = RULE_HEADING.match(line)
        if rule_match:
            flush()
            current_rule = rule_match.group("rule_id")
            current_title = rule_match.group("title")
            buffer = []
            continue

        if current_rule is None:
            meta_match = META_LINE.match(line)
            if meta_match:
                meta[meta_match.group("key")] = meta_match.group("value")
            continue

        buffer.append(line)

    flush()

    spec_id = meta.get("id", "")
    if not spec_id and rules:
        spec_id = rules[0][0].rsplit("-R", 1)[0]
    if not spec_id:
        spec_id = path.stem.upper()

    tags = tuple(part.strip() for part in meta.get("tags", "").split(",") if part.strip())
    status = meta.get("status", "active")
    return SpecDocument(
        spec_id=spec_id,
        title=meta.get("title", title),
        status=status,
        tags=tags,
        source_file=source_file,
        rules=tuple(rules),
    )


def load_specs(specs_dir: Path, project_dir: Path) -> list[SpecDocument]:
    """读取规格目录下的全部规格文件。"""
    documents: list[SpecDocument] = []
    for path in sorted(specs_dir.glob("*.md")):
        source_file = path.relative_to(project_dir).as_posix()
        documents.append(parse_spec_document(path, source_file))
    return documents
