"""Build a portable Dify 1.9.1 Workflow (no marketplace dependencies)."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


class DslDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def represent_string(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value,
                                   style="|" if "\n" in value else None)


DslDumper.add_representer(str, represent_string)


def build():
    nodes, edges = [], []
    def node(identity, kind, title, **fields):
        position = {"x": 60 + len(nodes) * 310, "y": 240}
        nodes.append({"id": identity, "type": "custom", "width": 250, "height": 100,
            "position": position, "positionAbsolute": position, "sourcePosition": "right",
            "targetPosition": "left", "selected": False,
            "data": {"type": kind, "title": title, "desc": "", "selected": False, **fields}})
        if len(nodes) > 1:
            previous = nodes[-2]
            edges.append({"id": previous["id"] + "-" + identity, "source": previous["id"],
                "target": identity, "type": "custom", "sourceHandle": "source", "targetHandle": "target",
                "data": {"sourceType": previous["data"]["type"], "targetType": kind,
                         "isInIteration": False, "isInLoop": False}, "zIndex": 0})
    def code(identity, title, source, variables, outputs):
        node(identity, "code", title, code_language="python3", code=source,
             variables=[{"variable": k, "value_selector": v} for k, v in variables.items()],
             outputs={k: {"type": v, "children": None} for k, v in outputs.items()})
    def http(identity, title, path, body):
        node(identity, "http-request", title, method="post", url="{{#env.cabinet_url#}}" + path,
            authorization={"type": "no-auth"},
            headers="Content-Type:application/json\nAuthorization:Bearer {{#env.cabinet_token#}}",
            params="", body={"type": "json", "data": [{"key": "", "type": "text", "value": body}]},
            timeout={"connect": 10, "read": 45, "write": 10}, ssl_verify=True,
            retry_config={"retry_enabled": False, "max_retries": 0, "retry_interval": 1000})
    node("start", "start", "🎟️ 夜班来访", variables=[
        {"variable": "passport", "label": "私人通行证（8–64位字母数字，换会话使用同一张）",
         "type": "text-input", "required": True, "max_length": 64, "options": []},
        {"variable": "move", "label": "你想做什么？首次输入：开门", "type": "paragraph",
         "required": True, "max_length": 200, "options": []}])
    code("pack", "校验通行证与输入", '''import json
import re
def main(passport: str, move: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", passport):
        raise ValueError("通行证须为8–64位字母、数字、下划线或短横线。请使用随机通行证并保密。")
    if len(move) > 200:
        raise ValueError("单次输入最多200字。")
    return {"body": json.dumps({"passport": passport}), "move": move}
''', {"passport": ["start", "passport"], "move": ["start", "move"]}, {"body": "string", "move": "string"})
    http("load", "📖 读取 PowerContext 旧档案", "/memory/load", "{{#pack.body#}}")
    code("game", "🎭 夜班规则：旧梗影响新回合", ROOT.joinpath("game.py").read_text(encoding="utf-8"),
        {"memory": ["load", "body"], "move": ["pack", "move"], "passport": ["start", "passport"],
         "request_id": ["sys", "workflow_run_id"]}, {"payload": "string", "result": "string"})
    http("commit", "💾 保存玩家选择和新状态", "/memory/commit", "{{#game.payload#}}")
    code("confirm", "确认保存成功再反馈", '''import json
def main(body: str, status: int) -> dict:
    value = json.loads(body)
    if status != 200:
        raise ValueError(str(value.get("detail", "回合未确认保存，请看看后重试。")))
    return {"body": json.dumps({"text": value["text"]}, ensure_ascii=False),
            "text": value["text"], "version": value["version"],
            "citation": json.dumps(value["citation"], ensure_ascii=False)}
''', {"body": ["commit", "body"], "status": ["commit", "status_code"]},
         {"body": "string", "text": "string", "version": "number", "citation": "string"})
    http("narrate", "✨ 可选 AI 旁白（默认规则剧场）", "/narrate", "{{#confirm.body#}}")
    nodes[-1]["data"].update(error_strategy="default-value", default_value=[
        {"key": "body", "type": "string", "value": "{}"},
        {"key": "status_code", "type": "number", "value": 503}])
    code("render", "展示回执与运行模式", '''import json
def main(body: str, status: int, saved_text: str) -> dict:
    try:
        value = json.loads(body)
        if status == 200 and isinstance(value.get("text"), str):
            return {"text": value["text"]}
    except (ValueError, TypeError):
        pass
    return {"text": saved_text + "\\n\\n🎭 旁白服务暂不可用，回合已保存。"}
''', {"body": ["narrate", "body"], "status": ["narrate", "status_code"],
         "saved_text": ["confirm", "text"]}, {"text": "string"})
    node("end", "end", "🧾 柜员回执", outputs=[
        {"variable": "text", "value_selector": ["render", "text"], "value_type": "string"},
        {"variable": "memory_version", "value_selector": ["confirm", "version"], "value_type": "number"},
        {"variable": "memory_citation", "value_selector": ["confirm", "citation"], "value_type": "string"}])
    document = {"kind": "app", "version": "0.4.0", "dependencies": [],
        "app": {"name": "怪奇失物招领处", "description":
            "把一件离谱物品交给夜班柜员阿纸。寄存、约定、认领、结案；换个会话，旧梗依然在。默认规则模式，无模型密钥亦可玩；可选AI旁白。",
            "icon": "🗃️", "icon_background": "#E8E5FF", "mode": "workflow", "use_icon_as_answer_icon": False},
        "workflow": {"conversation_variables": [], "environment_variables": [
            {"id": "d9c54ad9-73bc-4433-afd3-96836e24e31e", "name": "cabinet_url", "value_type": "string",
             "value": "http://host.docker.internal:8812", "selector": ["env", "cabinet_url"],
             "description": "适配器地址；从Dify API容器内必须可达"},
            {"id": "a9bcc160-d9c3-42ca-92c0-eb2f515e60d5", "name": "cabinet_token", "value_type": "secret",
             "value": "", "selector": ["env", "cabinet_token"], "description": "与CABINET_TOKEN保持一致，导入后填写"}],
            "features": {"file_upload": {"enabled": False}, "opening_statement": "",
                "retriever_resource": {"enabled": False}, "sensitive_word_avoidance": {"enabled": False},
                "speech_to_text": {"enabled": False}, "suggested_questions": [],
                "suggested_questions_after_answer": {"enabled": False}, "text_to_speech": {"enabled": False}},
            "graph": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.65}}}}
    output = ROOT / "dsl" / "strange-cabinet.yml"
    output.parent.mkdir(exist_ok=True)
    output.write_text(yaml.dump(document, Dumper=DslDumper, allow_unicode=True,
                                sort_keys=False, width=110), encoding="utf-8")
    return output


if __name__ == "__main__":
    print(build())
