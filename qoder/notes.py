"""Markdown note segmentation and optional LLM candidate generation.

Candidates never touch PowerContext: only confirmed cards are imported.
"""

import hashlib
import json
import os
import re

import httpx
from pydantic import BaseModel, ConfigDict, Field

from domain import CardInput

MAX_NOTE_BYTES = 64 * 1024
MIN_SEGMENT_CHARS = 20
MAX_SEGMENT_CHARS = 2000
MAX_CANDIDATES_PER_SEGMENT = 5

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
BLANK_RE = re.compile(r"^\s*$")


class SegmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=1)
    topic: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=200)


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    heading: str | None
    text: str


class CandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segments: list[Segment] = Field(min_length=1, max_length=12)
    topic: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=200)


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_index: int
    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(min_length=1, max_length=700)


def split_note(note: str) -> list[Segment]:
    """Split Markdown into heading-led blocks, merging run-on lines."""
    lines = note.replace("\r\n", "\n").split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    current_heading: str | None = None
    for line in lines:
        match = HEADING_RE.match(line)
        if match:
            if current:
                blocks.append([current_heading or "", *current])
            current_heading = match.group(2).strip() or match.group(0).strip()
            current = []
            continue
        if BLANK_RE.match(line):
            continue
        current.append(line.strip())
    if current:
        blocks.append([current_heading or "", *current])

    segments: list[Segment] = []
    index = 0
    for block in blocks:
        heading = block[0] or None
        text = " ".join(block[1:]).strip()
        if len(text) < MIN_SEGMENT_CHARS:
            continue
        if len(text) > MAX_SEGMENT_CHARS:
            text = text[:MAX_SEGMENT_CHARS] + "…"
        segments.append(Segment(index=index, heading=heading, text=text))
        index += 1
    return segments


def stable_card_id(topic: str, question: str, answer: str, source: str) -> str:
    """Content-derived identity: the same note content maps to the same card."""
    canonical = "\n".join([topic.strip(), question.strip(), answer.strip(), source.strip()])
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"note-{digest}"


def llm_config() -> dict | None:
    base_url = os.getenv("RECALL_LLM_BASE_URL")
    api_key = os.getenv("RECALL_LLM_API_KEY")
    model = os.getenv("RECALL_LLM_MODEL")
    if not (base_url and api_key and model):
        return None
    return {"base_url": base_url.rstrip("/"), "api_key": api_key, "model": model}


CANDIDATE_PROMPT = """你是复习卡片生成助手。根据给定的笔记片段生成自测卡片。
只输出 JSON 数组，每个元素为 {{"question": "...", "answer": "..."}}，不要输出其他文字。
要求：问题考察该片段的核心知识点；答案准确、简洁，基于片段原文；每段最多 3 张卡片。
片段：{segment}"""


async def generate_candidates(request: CandidateRequest) -> list[Candidate]:
    config = llm_config()
    if config is None:
        raise RuntimeError(
            "未配置模型 API。设置 RECALL_LLM_BASE_URL、RECALL_LLM_API_KEY、RECALL_LLM_MODEL "
            "三个环境变量后可自动生成候选；也可以直接在分段结果中手工填写问题与答案。"
        )
    candidates: list[Candidate] = []
    async with httpx.AsyncClient(base_url=config["base_url"], timeout=60, trust_env=False) as client:
        for segment in request.segments:
            payload = {
                "model": config["model"],
                "messages": [{"role": "user", "content": CANDIDATE_PROMPT.format(segment=segment.text)}],
                "temperature": 0.2,
            }
            response = await client.post(
                "/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {config['api_key']}"},
            )
            if response.status_code >= 400:
                raise RuntimeError(f"模型 API 返回 HTTP {response.status_code}，请检查模型配置与服务日志。")
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```[a-zA-Z]*\s*", "", content).replace("```", "").strip()
            try:
                items = json.loads(content)
            except json.JSONDecodeError as error:
                raise RuntimeError("模型返回的内容不是有效 JSON，请重试或改用分段结果手工填写。") from error
            if not isinstance(items, list):
                raise RuntimeError("模型返回格式不符合预期，请重试或改用分段结果手工填写。")
            for item in items[:MAX_CANDIDATES_PER_SEGMENT]:
                question = str(item.get("question", "")).strip()
                answer = str(item.get("answer", "")).strip()
                if not question or not answer:
                    continue
                try:
                    CardInput(
                        id=stable_card_id(request.topic, question, answer, request.source),
                        topic=request.topic,
                        question=question,
                        answer=answer,
                        source=request.source,
                    )
                except ValueError:
                    continue
                candidates.append(Candidate(segment_index=segment.index, question=question, answer=answer))
    return candidates
