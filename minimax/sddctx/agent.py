"""MiniMax 接入：把检索到的上下文交给模型，产出并应用代码改动。

调用方式为 MiniMax 开放接口（HTTP），实际使用的模型、请求与响应都会落盘，
便于评审核对「模型确实参与了本次代码修改」。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sddctx.config import MiniMaxSettings
from sddctx.retrieval import ContextBundle

DIFF_BLOCK = re.compile(r"```(?:diff|patch)?\s*\n(?P<diff>.*?)```", re.DOTALL)
FENCE_BLOCK = re.compile(r"```(?:python)?\s*\n(?P<code>.*?)```", re.DOTALL)

SYSTEM_PROMPT = (
    "你是一名资深 Python 工程师。你会收到一份开发任务的上下文，包含适用规格、"
    "待修改的代码与验证测试。请严格按照规格实现代码改动，只修改必要的代码，"
    "保持既有行为不变。输出必须是一个可用的 unified diff，且仅包含针对仓库根目录的"
    "相对路径，不要输出解释性文字。"
)


@dataclass
class AgentResult:
    """一次模型协作的结果。"""

    task_id: str
    model: str
    request_path: str
    response_path: str
    diff_path: str
    applied: bool
    changed_files: list[str] = field(default_factory=list)
    raw_response: str = ""
    error: str = ""


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class MiniMaxClient:
    """MiniMax 开放接口客户端。"""

    def __init__(self, settings: MiniMaxSettings) -> None:
        self._settings = settings

    @property
    def model(self) -> str:
        return self._settings.model

    def chat(self, system: str, user: str, *, timeout: float = 180.0) -> tuple[str, dict[str, Any]]:
        """发起一次对话，返回 (正文, 原始响应)。"""
        if not self._settings.api_key:
            raise RuntimeError(
                "未配置 MINIMAX_API_KEY，无法调用 MiniMax。请在运行说明所述位置提供密钥。"
            )
        import httpx

        url = f"{self._settings.base_url.rstrip('/')}/v1/text/chatcompletion_v2"
        payload = {
            "model": self._settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        base_resp = data.get("base_resp") or {}
        if base_resp.get("status_code") not in (0, None):
            raise RuntimeError(
                f"MiniMax 返回错误：{base_resp.get('status_code')} {base_resp.get('status_msg', '')}"
            )
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"MiniMax 响应中没有 choices：{json.dumps(data, ensure_ascii=False)[:500]}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content)
        return str(content), data


def build_user_prompt(bundle: ContextBundle, project_dir: Path, files: list[str]) -> str:
    """把上下文拼成给模型的提示词。"""
    parts: list[str] = []
    parts.append(f"# 开发任务\n{bundle.task}\n")

    parts.append("# 适用规格")
    for rule in bundle.specs:
        parts.append(f"## {rule.rule_id} {rule.rule_title}（来源 {rule.source_file}）")
        parts.append(rule.body)
    parts.append("")

    parts.append("# 需要修改的代码")
    for relative in files:
        path = project_dir / relative
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        parts.append(f"## {relative}")
        parts.append("```python")
        parts.append(content)
        parts.append("```")
    parts.append("")

    parts.append("# 验证测试（必须全部通过，且修改前它们会失败）")
    for test in bundle.target_tests:
        parts.append(f"## {test.file}::{test.name}")
        parts.append("```python")
        parts.append(test.body)
        parts.append("```")
    parts.append("")

    parts.append(
        "# 输出要求\n"
        "1. 只输出一个 unified diff，路径相对仓库根目录，例如 `a/src/orderkit/repository.py`。\n"
        "2. diff 必须可以由 `git apply -p1` 直接应用。\n"
        "3. 实现规格中的全部规则，包括异常类型、错误消息内容与边界行为。\n"
        "4. 不要修改测试文件，不要删除既有行为。"
    )
    return "\n".join(parts)


def extract_diff(raw: str) -> str:
    """从模型回复中取出 diff。"""
    match = DIFF_BLOCK.search(raw)
    if match:
        candidate = match.group("diff").strip()
        if candidate:
            return candidate + "\n"
    lines = raw.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith("diff --git") or line.startswith("--- "):
            start = index
            break
    if start is None:
        return ""
    collected: list[str] = []
    for line in lines[start:]:
        if line.startswith("```"):
            break
        collected.append(line)
    return "\n".join(collected).strip() + "\n"


def _git_executable() -> str:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("未找到 git 可执行文件，无法应用模型产出的 diff。")
    return git


def apply_diff(project_dir: Path, diff: str) -> tuple[bool, str]:
    """在工程目录内应用 diff。"""
    patch_file = project_dir / ".sddctx.patch"
    patch_file.write_text(diff, encoding="utf-8")
    git = _git_executable()
    try:
        check = subprocess.run(
            [git, "apply", "--check", "-p1", "--whitespace=nowarn", str(patch_file)],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            retry = subprocess.run(
                [git, "apply", "--check", "-p1", "--unidiff-zero", "--whitespace=nowarn", str(patch_file)],
                cwd=project_dir,
                capture_output=True,
                text=True,
            )
            if retry.returncode != 0:
                return False, (retry.stderr or check.stderr).strip()
            apply_result = subprocess.run(
                [git, "apply", "-p1", "--unidiff-zero", "--whitespace=nowarn", str(patch_file)],
                cwd=project_dir,
                capture_output=True,
                text=True,
            )
        else:
            apply_result = subprocess.run(
                [git, "apply", "-p1", "--whitespace=nowarn", str(patch_file)],
                cwd=project_dir,
                capture_output=True,
                text=True,
            )
        if apply_result.returncode != 0:
            return False, apply_result.stderr.strip()
        return True, ""
    finally:
        patch_file.unlink(missing_ok=True)


def changed_files(project_dir: Path, diff: str) -> list[str]:
    """从 diff 中解析被修改的文件。"""
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            relative = line[6:].strip()
            if relative and relative not in files:
                files.append(relative)
    return files


class MiniMaxAgent:
    """把上下文交给 MiniMax 并应用其产出的改动。"""

    def __init__(self, settings: MiniMaxSettings, project_dir: Path, data_dir: Path) -> None:
        self._settings = settings
        self._project_dir = project_dir
        self._evidence_dir = data_dir / "agent"
        self._client = MiniMaxClient(settings)

    def run(self, bundle: ContextBundle, files: list[str], task_id: str) -> AgentResult:
        """执行一次模型协作。"""
        evidence_dir = self._evidence_dir / task_id
        evidence_dir.mkdir(parents=True, exist_ok=True)

        user_prompt = build_user_prompt(bundle, self._project_dir, files)
        request_payload = {
            "model": self._settings.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        }
        request_path = evidence_dir / "request.json"
        request_path.write_text(
            json.dumps(request_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        response_path = evidence_dir / "response.json"
        diff_path = evidence_dir / "change.diff"

        try:
            content, raw = self._client.chat(SYSTEM_PROMPT, user_prompt)
        except Exception as error:
            response_path.write_text(
                json.dumps({"error": str(error), "at": _now()}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return AgentResult(
                task_id=task_id,
                model=self._settings.model,
                request_path=str(request_path),
                response_path=str(response_path),
                diff_path=str(diff_path),
                applied=False,
                error=str(error),
            )

        response_path.write_text(
            json.dumps({**raw, "captured_at": _now()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        diff = extract_diff(content)
        diff_path.write_text(diff, encoding="utf-8")
        if not diff:
            return AgentResult(
                task_id=task_id,
                model=self._settings.model,
                request_path=str(request_path),
                response_path=str(response_path),
                diff_path=str(diff_path),
                applied=False,
                raw_response=content,
                error="模型回复中没有可用的 unified diff",
            )

        applied, error = apply_diff(self._project_dir, diff)
        return AgentResult(
            task_id=task_id,
            model=self._settings.model,
            request_path=str(request_path),
            response_path=str(response_path),
            diff_path=str(diff_path),
            applied=applied,
            changed_files=changed_files(self._project_dir, diff) if applied else [],
            raw_response=content,
            error=error,
        )
