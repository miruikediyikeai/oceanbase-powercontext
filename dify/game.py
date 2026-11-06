"""This exact source is embedded in the Dify Code node. Standard library only."""
import copy
import json


def fresh_state():
    return {"schema": 1, "turn": 0, "score": 0, "items": [], "vow": "", "album": [], "visits": 0}


def main(memory: str, move: str, passport: str, request_id: str) -> dict:
    envelope = json.loads(memory)
    if "state" not in envelope:
        raise ValueError("记忆读取失败：请检查读取节点 HTTP 状态。")
    state = copy.deepcopy(envelope["state"] or fresh_state())
    move = move.strip()
    title = "🗃️ 怪奇失物招领处 · 夜班柜员阿纸"
    messages = []
    def say(text):
        messages.append(text)
    if move in ("", "帮助", "开门", "看看"):
        state["visits"] += 1
        if state["visits"] > 1:
            say("你又来了。别装第一次来，你的档案夹自己跑到柜台上了。")
        else:
            say("欢迎来到不存在的负一楼。这里寄存的不是东西，是东西惹出来的麻烦。")
        if state["vow"]:
            say("我还记着你的约定：「" + state["vow"] + "」。违约的话，订书机会打嗝。")
        if state["album"]:
            old = state["album"][-1]
            say("上次结案你拿到了「" + old["title"] + "」，那件「" + old["souvenir"] + "」至今还在员工培训里当反面教材。")
        say("**三步玩一局**\n1. `寄存 物品名 | 离谱来历`（最多三件）\n2. `约定 一句约定`，或 `认领 物品名 | 包含来历中至少两个连续字的证词`\n3. `结案`，领取专属收据。\n随时输入 `看看` 回访；同一张通行证换个会话仍能接上。")
    elif move.startswith("寄存 "):
        parts = move[3:].split("|", 1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            say("柜员把空白表格推回来：格式是 `寄存 物品名 | 离谱来历`。")
        else:
            name, origin = (part.strip() for part in parts)
            if len(name) > 28 or len(origin) > 100 or len("".join(c for c in origin if c.isalnum())) < 2:
                say("这张表装不下：物品名最多28字，来历最多100字，且至少含两个文字或数字。")
            elif any(item["name"] == name for item in state["items"]):
                say("「" + name + "」已经在柜子里了。请别把同一个宇宙折叠两次。")
            elif len(state["items"]) >= 3:
                say("柜子已满。先结案开下一班，三件东西已经让抽屉长腿了。")
            else:
                state["items"].append({"name": name, "origin": origin, "claimed": False})
                state["turn"] += 1
                state["score"] += 2
                say("已寄存「" + name + "」。来历：「" + origin + "」。\n阿纸盖章：本处第一次接待这种麻烦，保管费收你两个不存在的硬币。离谱值 +2。")
                if len(state["items"]) >= 2:
                    prior = state["items"][-2]
                    say("连锁事故：它和「" + prior["name"] + "」住隔壁。前者负责「" + origin + "」，后者负责「" + prior["origin"] + "」——物业已经辞职。")
    elif move.startswith("约定 "):
        vow = move[3:].strip()
        if not vow or len(vow) > 100:
            say("约定要有内容，而且最多100字。")
        else:
            old = state["vow"]
            state["vow"] = vow
            state["turn"] += 1
            say("我把「" + vow + "」写进了值班守则。下次你来，我会主动提醒。")
            if old:
                say("原约定「" + old + "」已被替换，旧订书机申请退休。")
    elif move.startswith("认领 "):
        parts = move[3:].split("|", 1)
        item = next((x for x in state["items"] if x["name"] == parts[0].strip()), None)
        if len(parts) != 2:
            say("请递交 `认领 物品名 | 证词`。证明你知道它的来历就行。")
        elif not item:
            say("查无此物。先输入 `看看` 核对物品名。")
        elif item["claimed"]:
            say("「" + item["name"] + "」已经认领，不能重复赚离谱值。")
        else:
            proof = parts[1].strip()
            if len(proof) > 150:
                say("证词最多150字，柜员没有那么长的午休。")
            else:
                origin = "".join(c for c in item["origin"] if c.isalnum())
                evidence = "".join(c for c in proof if c.isalnum())
                matched = next((origin[i:i + 2] for i in range(len(origin) - 1)
                                if origin[i:i + 2] in evidence), "")
                state["turn"] += 1
                if matched:
                    item["claimed"] = True
                    state["score"] += 3
                    say("证词核对通过：「" + matched + "」与原档案吻合。领回「" + item["name"] + "」，离谱值 +3。\n记忆回声：你当时写的是「" + item["origin"] + "」。我可没忘。")
                else:
                    say("驳回：证词没有和原来历对上。小提示：来历是「" + item["origin"] + "」。找出至少两个连续字，再来骗……再来认领。")
    elif move == "结案":
        if not state["items"]:
            say("没有物品不能结案。先寄存一件奇怪的东西吧。")
        else:
            count = sum(x["claimed"] for x in state["items"])
            badge = "宇宙级烂摊子收藏家" if state["score"] >= 10 else "负一楼常驻编外人员"
            souvenir = state["items"][0]["name"]
            say("### 🧾 夜班结案收据\n称号：**" + badge + "**\n离谱值：" + str(state["score"]) +
                " · 已认领：" + str(count) + "/" + str(len(state["items"])) +
                "\n本班展品：" + "、".join(x["name"] for x in state["items"]) +
                "\n留给下次的暗号：「" + souvenir + "」" +
                ("\n仍然有效的约定：「" + state["vow"] + "」" if state["vow"] else "") +
                "\n\n本班已归档，柜子已腾空。下次开门，阿纸会记得你的称号、暗号和约定。")
            state["album"] = (state["album"] + [{"title": badge, "souvenir": souvenir}])[-3:]
            state["items"], state["turn"], state["score"] = [], 0, 0
    else:
        say("阿纸把你的话夹进了‘尚未发明的业务’。输入 `看看` 获取玩法；业务前缀要有空格。")
    if move != "结案" and state["items"]:
        say("**柜中清单**\n" + "\n".join("- " + x["name"] + (" · 已领回" if x["claimed"] else " · 待认领") for x in state["items"]) +
            "\n离谱值：" + str(state["score"]))
    result = title + "\n\n" + "\n\n".join(messages)
    payload = {"passport": passport, "request_id": request_id,
               "expected_version": envelope["version"], "state": state, "result": result}
    return {"payload": json.dumps(payload, ensure_ascii=False), "result": result}
