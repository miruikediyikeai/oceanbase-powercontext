"""Boundary regressions for choices that would otherwise break a playable round."""
import json
from pathlib import Path
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from game import main


def move(text, state=None, version=0):
    value = main(json.dumps({"state": state, "version": version}), text, "test-passport", "test-run")
    payload = json.loads(value["payload"])
    return payload["state"], value["result"]


@pytest.mark.parametrize("origin", ["！", "月", "。 。", " ？ "])
def test_unprovable_origins_cannot_be_deposited(origin):
    state, text = move("寄存 月亮 | " + origin)
    assert state["items"] == []
    assert state["score"] == 0
    assert "至少含两个" in text


def test_json_quotes_and_pipes_survive_memory_roundtrip_without_changing_rules():
    state, _ = move('寄存 "月亮" | 他说"奶酪"|是周五的秘密')
    updated, text = move('认领 "月亮" | 奶酪', state, 1)
    assert updated["items"][0]["claimed"] is True
    assert updated["score"] == 5
    assert state["items"][0]["claimed"] is False
    assert '他说"奶酪"|是周五的秘密' in text


def test_fourth_item_does_not_displace_saved_inventory():
    state = None
    for name in ("月亮", "影子", "枕头"):
        state, _ = move(f"寄存 {name} | 来自月球", state)
    fourth, text = move("寄存 闹钟 | 吃掉昨天", state)
    assert fourth == state
    assert "柜子已满" in text


def test_empty_closure_never_erases_vow_or_album():
    state, _ = move("约定 先说咕叽")
    after, text = move("结案", state)
    assert after == state
    assert "没有物品不能结案" in text


def test_imported_dsl_executes_the_delivered_game_source():
    document = yaml.safe_load((ROOT / "dsl/strange-cabinet.yml").read_text(encoding="utf-8"))
    nodes = document["workflow"]["graph"]["nodes"]
    assert next(n["data"]["code"] for n in nodes if n["id"] == "game") == (ROOT / "game.py").read_text(encoding="utf-8")
    assert document["dependencies"] == []
    for node in nodes:
        if node["data"]["type"] == "code":
            compile(node["data"]["code"], node["id"], "exec")
