"""工程资料的数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SpecRule:
    """一条规格规则。规则编号在工程内稳定，导入时以其为主键。"""

    spec_id: str
    spec_title: str
    spec_status: str
    rule_id: str
    rule_title: str
    body: str
    tags: tuple[str, ...]
    source_file: str
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CodeSymbol:
    """代码中的类、函数或方法。"""

    symbol: str
    kind: str
    file: str
    line: int
    signature: str
    doc: str
    body: str

    @property
    def qualified(self) -> str:
        return f"{self.file}::{self.symbol}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TestCase:
    """一条测试用例。targets 记录它直接覆盖的代码符号。"""

    name: str
    file: str
    line: int
    doc: str
    body: str
    targets: tuple[str, ...]

    @property
    def qualified(self) -> str:
        return f"{self.file}::{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskState:
    """任务状态：可写入 PowerContext 并在进程重启后取回。"""

    task_id: str
    title: str = ""
    description: str = ""
    stage: str = "created"
    specs: list[str] = field(default_factory=list)
    code: list[str] = field(default_factory=list)
    target_tests: list[str] = field(default_factory=list)
    regression_tests: list[str] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)
    verification: list[dict[str, Any]] = field(default_factory=list)
    context_path: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskState:
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in payload.items() if key in known})
