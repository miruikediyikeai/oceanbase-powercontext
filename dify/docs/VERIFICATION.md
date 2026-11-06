# 完整验证样例

## 初始条件

- 按 RUNBOOK 启动真实 PowerContext 和适配器，在 Dify 1.9.1 导入 DSL 并填好两个工作流环境变量。
- 使用一张从未使用过的随机通行证，例如运行 `python -c "import secrets; print(secrets.token_urlsafe(24))"` 获取。不要使用文档里固定的公开字符串作为真实存档凭据。
- 默认不配置模型，预期每次正文末尾显示“规则剧场 · 本回合未调用生成模型”。
- 新通行证对应空 scope，没有物品、得分、约定或相册；不需要下载任何数据。
- 下表中所有操作使用同一张通行证，按顺序执行。进入新会话时仍填写相同通行证。

## 一组完整体验

| 步骤 | 输入 `move` | 应核对的表现 |
| --- | --- | --- |
| 1 | `开门` | 阿纸欢迎来访并展示三步玩法；新存档第 1 版 |
| 2 | `寄存 会打嗝的月亮 | 每周五变成奶酪` | 物品与来历出现在回执；得分 2 |
| 3 | `寄存 拒绝加班的影子 | 下班后替我去月球钓鱼` | 得分 4；出现同时引用两件物品及原来历的“连锁事故” |
| 4 | `约定 每次见面先说咕叽` | 回执确认写入值班守则 |
| 5 | `认领 会打嗝的月亮 | 这只是一个普通气球` | 驳回证词，提示原来历；得分仍为 4 |
| 6 | `认领 会打嗝的月亮 | 奶酪是它的周五皮肤` | 原记忆包含“奶酪”，因此通过；引用原来历并加 3，得分 7 |
| 7 | 再输入步骤 6 | 提示已经认领，得分仍为 7 |
| 8 | `看看` | 主动提起“每次见面先说咕叽”，物品认领状态仍在 |
| 9 | `结案` | 显示夜班结案收据；称号“负一楼常驻编外人员”，离谱值 7，认领 1/2；柜子腾空 |
| 10 | **新会话**，相同通行证，输入 `开门` | 同时提起旧约定“咕叽”、上一班称号及暗号“会打嗝的月亮” |

如要取得更高称号，在步骤 9 前认领第二件物品：`认领 拒绝加班的影子 | 月球钓鱼是我的爱好`。总分达到 10，获得“宇宙级烂摊子收藏家”。

所有输出由规则与实际存档共同决定；AI 模式可以额外出现不同旁白，但物品、得分与归档结果应相同。Dify End 节点输出 `memory_version` 和 `memory_citation`，可打开执行详情查看每一步的读取、Code 计算、提交和响应。

## 核对 PowerContext 的实际作用

三个观察点缺一不可：

1. 寄存物品时，Dify `game` 节点把玩家输入写入 `state.items`，`commit` HTTP 节点返回真实 PowerContext citation；不是只检查连接成功。
2. 认领时，`load` 返回旧物品和旧来历，Code 节点从该来历中寻找证词的连续两个字，实际影响通过 / 驳回与得分。
3. 结案后换会话或重启两个配套进程，再读取相同 scope 的相册和约定，回执主动复用旧内容。

需要直接检查时，在服务机器上通过适配器 `/memory/load` 获取 `scope_id` 和 `citation`，再调用真实 PowerContext：

```http
POST http://127.0.0.1:8811/v1/memory/entries/list
Content-Type: application/json

{"scope_id":"替换成实际 scope_id"}
```

`kind=cabinet_state` 的 `text` 是 JSON，可核对 `state.vow`、`state.items`、`state.album` 与 `version`。使用受认证的 PowerContext 时需附实际 Bearer token。不要将真实服务凭据或玩家通行证写入公开证据。

## 重启、隔离、并发和故障样例

- 完成步骤 9，记录 `/memory/load` 的结果，Ctrl+C 停止两个服务；保留 `.data/memory/`，重新启动。重新读取必须得到相同的 state、version、scope 和 citation，再执行步骤 10。
- 换一张新通行证输入 `开门`，不应提起旧物品或约定。
- 读取版本 v 后提交一轮，再以 v 和一个不同 `request_id` 提交，应该 HTTP 409，存档不被覆盖。
- 对最后一次成功提交使用相同 `request_id` 和原请求体重试，返回 `replayed=true`，版本和得分不变。该请求 ID 来自 Dify 的 `sys.workflow_run_id`。
- 停止 PowerContext，读取应 HTTP 503，不得返回伪造的空存档；恢复后先 `看看` 核对。
- 模型未配置时依然可完成全部步骤；模型故障只影响附加旁白。合成 HTTP 失败响应的测试见 `tests/test_narration.py`，不代表真实模型调用。

## 自动执行

真实 PowerContext + HTTP 适配器 + DSL 内相同源码：

```powershell
.\.venv\Scripts\python.exe scripts/verify.py --output .runtime/my-verification
```

脚本自动选择空闲端口，在指定目录创建一套隔离的真实 PowerContext 数据，执行十轮、重启两个进程、核对完全一致的状态与 citation，检查直接 PowerContext API 读回值及故障行为。仅终止自己启动的进程，不占用或删除你的游戏数据。默认不连接 Dify；它不能证明 DSL 已成功导入。

真实已发布 Dify 的七轮精简验收：

```powershell
# .env 中配置 DIFY_API_URL 和 DIFY_APP_KEY
.\.venv\Scripts\python.exe scripts/verify_dify.py --output .runtime/dify-verification.json
```

脚本通过真实 `/v1/workflows/run` 调用，检查所有运行 succeeded、业务反馈与 citation，并以不同的 Dify API `user` 验证相同通行证的跨用户上下文复用。只会在全部成功后写出 passed 证据，无法连接或节点失败就退出失败，不会伪造执行记录。

## 恢复初始状态

首选生成新通行证；每次验证脚本默认也会生成新值。无需清空数据库，更不会影响其他玩家。`结案` 只开启新班次，不是删除跨会话记忆。网页“换张通行证”仅更换当前存档标识。

## 当前实际结果

实际的十轮输出、PowerContext 版本引用、重启前后快照和 API 直接读回见 `evidence/powercontext-verification.json`。规则及模型失败契约测试结果见 `evidence/tests.txt`。真实 Dify 引擎验收尚未完成，不能将上述材料当作 Dify 导入成功证据，原因见 `evidence/README.md`。
