"""Provider contract tests with synthetic HTTP responses, not evidence of model calls."""
import asyncio
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


@pytest.mark.parametrize("payload,status", [
    ({"choices": []}, 200),
    ({"choices": [{"message": {"content": None}}]}, 200),
    ({"choices": [{"message": {"content": ""}}]}, 200),
    ({"error": "provider unavailable"}, 503),
])
def test_provider_failure_keeps_original_committed_receipt(monkeypatch, payload, status):
    for name, value in (("NARRATOR_BASE_URL", "https://model.example/v1"),
                        ("NARRATOR_MODEL", "contract-test"), ("NARRATOR_API_KEY", "synthetic-key")):
        monkeypatch.setenv(name, value)
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
    monkeypatch.setattr(app.httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    result = asyncio.run(app.narrate(app.Narration(text="已保存：月亮，离谱值2")))
    assert result["mode"] == "rules-fallback"
    assert result["text"].startswith("已保存：月亮，离谱值2")
    assert "回合已保存" in result["text"]
