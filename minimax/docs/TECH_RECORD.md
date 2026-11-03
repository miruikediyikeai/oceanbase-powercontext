# 技术使用记录

本文件记录本次任务中三项技术的实际使用情况，所有数字与响应均来自真实运行。

## 1. seekdb / OceanBase

### 1.1 实例

| 项 | 值 |
| --- | --- |
| 运行环境 | WSL2 Ubuntu-22.04 |
| 版本 | `5.7.25-OceanBase seekdb-v1.4.0.0` |
| 运行目录 | `/home/bit/seekdb-run/seekdb1` |
| SQL 端口 | `10000`（MySQL 协议） |
| 遥测 ID | `fb0a292a-08d2-8497-b153-c291b31f1e55`（取自 `run/telemetry.json` 的 `content.id`） |
| 资料库 | `sdd_context`（规格变更演示使用独立库 `sdd_context_spec_demo`） |

### 1.2 连接验证

```bash
$ mysql --no-defaults --protocol=TCP --connect-timeout=10 --host=172.29.132.88 \
    --port=10000 --user=root --batch \
    --execute="SELECT VERSION() AS version, 1+1 AS sql_check, DATABASE() AS db, NOW() AS server_time;"
version	sql_check	db	server_time
5.7.25-OceanBase seekdb-v1.4.0.0	2	sdd_context	2026-11-03 17:03:06
```

### 1.3 保存了什么

`python -m sddctx import` 解析 `sample_project/` 后写入：

| 表 | 行数 | 内容 |
| --- | --- | --- |
| `spec_rule` | 9 | 3 份规格共 9 条规则的正文、来源文件与内容指纹 |
| `code_symbol` | 10 | 3 个源文件中的类、方法、函数签名与源码片段（含改动后新增的 `OrderRepository._parse_status`） |
| `test_case` | 13 | 13 条测试用例及其直接覆盖的符号 `targets` |
| `import_batch` | 每次导入一条 | 导入批次、来源目录与资料数量 |
| `import_record` | 逐条 | 每条资料在每个批次中的 inserted / updated / unchanged / removed |

重新导入时，模块会删除工程里已经不存在的资料（`removed`），因此库内内容始终等于
当前工程的内容。完整记录见 `evidence/database/<时间戳>/database-usage.json`。

### 1.4 查询如何进入上下文

上下文不是从文件现读的，而是通过 SQL 取回的。以任务
「为 OrderRepository.list_orders 增加按订单状态筛选的能力……规格见 SPEC-ORD-003。」
为例，实际执行的检索语句形如：

```sql
SELECT spec_id, rule_id, spec_title, spec_status, rule_title, body, tags,
       source_file, content_hash,
       (CASE WHEN body LIKE %s THEN 1 ELSE 0 END
        + CASE WHEN rule_title LIKE %s THEN 1 ELSE 0 END
        + CASE WHEN spec_title LIKE %s THEN 1 ELSE 0 END
        + CASE WHEN tags LIKE %s THEN 1 ELSE 0 END) AS score
FROM spec_rule
WHERE (body LIKE %s OR rule_title LIKE %s OR spec_title LIKE %s OR tags LIKE %s)
ORDER BY score DESC, spec_id, rule_id
LIMIT %s;
```

命中的规则（真实得分）：

| 规则 | 得分 | 来源文件 |
| --- | --- | --- |
| `SPEC-ORD-003-R1` | 10 | `specs/SPEC-ORD-003-order-status-filter.md` |
| `SPEC-ORD-003-R2` | 8 | `specs/SPEC-ORD-003-order-status-filter.md` |
| `SPEC-ORD-003-R4` | 7 | `specs/SPEC-ORD-003-order-status-filter.md` |
| `SPEC-ORD-003-R3` | 4 | `specs/SPEC-ORD-003-order-status-filter.md` |

代码符号检索使用同一套打分方式，并额外按规格正文里出现的标识符
（`OrderRepository`、`list_orders`）做名称匹配：

```sql
SELECT symbol, kind, file, line, signature, doc, body, content_hash,
       (CASE WHEN symbol = %s OR symbol LIKE %s THEN 5 ELSE 0 END
        + CASE WHEN symbol LIKE %s THEN 1 ELSE 0 END
        + CASE WHEN signature LIKE %s THEN 1 ELSE 0 END
        + CASE WHEN doc LIKE %s THEN 1 ELSE 0 END) AS score
FROM code_symbol
WHERE ((symbol = %s OR symbol LIKE %s) OR (symbol LIKE %s OR signature LIKE %s OR doc LIKE %s))
ORDER BY score DESC, file, line
LIMIT %s;
```

测试用例按 `targets` 与「需要修改的代码」的交集判定目标测试，其余为回归测试，
两条路径都落在 `test_case` 表的 SQL 查询上。

## 2. PowerContext

### 2.1 版本与接入方式

| 项 | 值 |
| --- | --- |
| 版本 | `powercontext[server]==1.2.0` |
| 接入方式 | 本地 HTTP 服务 + Python SDK `powercontext.client.PowerContextClient` |
| 服务地址 | `http://127.0.0.1:8811` |
| 存储 | SQLite（`.data/powercontext/memory.db`） |
| scope | `sdd-context`（幂等创建；一次运行的实测 scope id 为 `scp_28jde4jvfbd50v0jqxbc8d96g5`） |

启动命令：

```bash
python scripts/start_powercontext.py --port 8811 --data .data/powercontext
```

### 2.2 写入任务状态

对应接口 `POST /v1/memory/remember`，请求体形如：

```json
{
  "scope_id": "scp_28jde4jvfbd50v0jqxbc8d96g5",
  "kind": "working_note",
  "text": "sddctx.task_state task_id=T-001 stage=context_ready\n{\"task_id\": \"T-001\", ...}",
  "reason": "sddctx task state T-001 stage=context_ready"
}
```

实测响应（取自 `evidence/verification/<时间戳>/04-state-save-context.log`）：

```json
{
  "scope_id": "scp_28jde4jvfbd50v0jqxbc8d96g5",
  "memory": {"family": "memory", "artifact_id": "memory", "revision": 2},
  "citation": {
    "memory_ref": {"family": "memory", "artifact_id": "memory", "revision": 2},
    "entry_id": "mem_ent_ddda0b22f8ee40749e14834ca79ede4e",
    "entry_version_id": "mem_ver_49c47c62df654396993c721562da9033"
  }
}
```

### 2.3 读回并复用

对应接口 `POST /v1/memory/entries/list`：读回当前 scope 的全部条目，按
`task_id` 过滤，取 `updated_at` 最新的一条，解析首行标记之后的 JSON。

实测（`evidence/verification/<时间戳>/07-state-read-back.log`）：该命令在一个
**全新进程**中执行，读回了上一个进程写入的内容：

```json
{
  "found": true,
  "source": "powercontext",
  "task_id": "T-001",
  "stage": "verification_failed",
  "specs": ["SPEC-ORD-001::SPEC-ORD-001-R2", "SPEC-ORD-003::SPEC-ORD-003-R1", "..."],
  "code": ["src/orderkit/models.py::OrderStatus", "src/orderkit/repository.py::OrderRepository", "..."],
  "target_tests": ["tests/test_repository_filter.py::test_list_orders_without_filter_returns_all", "..."],
  "regression_tests": ["tests/test_models.py::test_amount_is_unit_price_times_quantity", "..."],
  "done": ["导入并检索工程资料（5 条规格规则、3 个代码符号、6 条目标测试）", "..."],
  "todo": ["按规格实现代码改动", "..."],
  "verification": [{"kind": "target", "exit_code": 1, "...": "..."}]
}
```

状态里的 `specs`、`code`、`target_tests`、`regression_tests` 直接来自当次检索，
`done` / `todo` 记录已完成与待办事项，`verification` 记录测试命令与退出码，
因此重启后的进程可以直接继续该任务，而不必重新检索。

### 2.4 服务重启后仍然可读

`python scripts/powercontext_restart_demo.py` 会读取任务状态、停止本地
PowerContext 服务、用同一数据目录重新启动服务，再读取一次状态，结果写入
`evidence/powercontext/<时间戳>/restart-demo.json`：

| 项 | 实测值 |
| --- | --- |
| `stopped_ok` | `true` |
| `restarted_ok` | `true` |
| `state_found_after_restart` | `true` |
| `same_task` / `specs_preserved` / `code_preserved` / `target_tests_preserved` | `true` |

即任务状态落在服务之外的持久化存储（SQLite 文件）中，服务重启后依旧可用。

## 3. MiniMax

### 3.1 实际集成方式

本次代码改动由 **Qoder 桌面端助手会话（会话所用模型为 MiniMax）** 产出：会话拿到模块
检索出的上下文后给出改动，模块再用 `sddctx/agent.py` 的同一条流水线处理这份产出——
组装提示词 → 提取 unified diff → `git apply` 校验并应用 → 更新任务状态并落盘证据。

| 项 | 值 |
| --- | --- |
| 产出通道 | 助手会话（Qoder Desktop），模型为 MiniMax |
| 模块侧入口 | `python -m sddctx agent --task-id T-001 --from-file <模型回复文件>` |
| 目标模型（模块默认） | `MiniMax-M2` |
| 上下文来源 | `evidence/verification/<时间戳>/minimax/request.json` |
| 模型产出 | `evidence/verification/<时间戳>/minimax/response.json`（`integration: assistant-session`） |
| 应用后的差异 | `evidence/verification/<时间戳>/minimax/change.diff` |
| 应用结果 | `evidence/verification/<时间戳>/06-minimax-agent.log`：`applied: true`，`changed_files: ["src/orderkit/repository.py"]` |

模块同时实现了直接调用 MiniMax 开放接口的通道
（`POST {base_url}/v1/text/chatcompletion_v2`，模型 `MiniMax-M2`，
见 `sddctx/agent.py::MiniMaxClient`）。本机未配置 `MINIMAX_API_KEY`，
`api.minimaxi.com` 与 `api.minimax.chat` 可达但鉴权无法通过，因此本次留痕的调用走的是
会话产出通道：把会话给出的回复交给模块，模块按与开放接口完全相同的流程提取、应用
并留痕。除此之外，`agent` 子命令在配置了密钥时会走 HTTP 通道，无需改动任何代码。

### 3.2 提供给模型的上下文

模块把检索结果拼成一条提示词（`sddctx/agent.py::build_user_prompt`），实际内容记录在
`request.json` 的 `messages[1].content`（4199 字符），结构为：

| 段 | 内容 |
| --- | --- |
| 开发任务 | 任务描述全文 |
| 适用规格 | `SPEC-ORD-001-R2`、`SPEC-ORD-003-R1`～`R4` 的正文与来源文件 |
| 需要修改的代码 | `src/orderkit/models.py`、`src/orderkit/repository.py` 的当前内容 |
| 验证测试 | `tests/test_repository_filter.py` 中 6 条目标用例的完整代码 |
| 输出要求 | 只输出可被 `git apply -p1` 应用的 unified diff，且不得改动测试 |

其中规格、代码与测试三段全部来自 seekdb 的真实检索结果（见第 1 节），不是本地文件现读。

### 3.3 模型产出与应用的差异

模型产出是一个只改动 `src/orderkit/repository.py` 的 unified diff（全文见
`minimax/change.diff`）：新增可选参数 `status`，不传时返回全部订单；传入时把状态归一化为
`OrderStatus`（字符串忽略大小写）；非法状态抛出 `ValueError`，错误消息同时包含
`status` 与全部合法状态取值；筛选无匹配时返回空列表。测试文件与既有行为未改动。

### 3.4 修改前后的测试结果

| 阶段 | 命令 | 退出码 | 结果 |
| --- | --- | --- | --- |
| 修改前·目标测试 | `pytest tests/test_repository_filter.py::<6 条用例> -q` | 1 | 5 失败 1 通过，失败原因 `TypeError: OrderRepository.list_orders() got an unexpected keyword argument 'status'` |
| 修改前·回归测试 | `pytest tests/test_models.py tests/test_pricing.py -q` | 0 | 7 条全部通过 |
| 修改后·目标测试 | 同上 | 0 | 6 条全部通过 |
| 修改后·回归测试 | 同上 | 0 | 7 条全部通过 |

失败来自目标功能尚未实现（参数不存在），不是环境或依赖问题；两次运行的测试集完全一致，
没有删除断言、跳过测试或修改预期结果。原始 pytest 输出见
`evidence/verification/<时间戳>/pytest-logs/`。
