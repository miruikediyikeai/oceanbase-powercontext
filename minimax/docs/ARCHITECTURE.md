# 模块结构与数据模型

## 一、模块分层

```
sddctx/
├── specs.py      规格解析：Markdown → SpecDocument → SpecRuleView
├── code.py       代码解析：AST → CodeSymbol / TestCase
├── store.py      资料存储与检索：seekdb / OceanBase（MySQL 协议）
├── retrieval.py  上下文检索与组装：任务 → ContextBundle
├── state.py      任务状态：本地工作副本 + PowerContext Memory API
├── agent.py      MiniMax 接入：上下文 → 模型 → unified diff → 应用
├── models.py     数据模型
├── config.py     环境变量配置
└── cli.py        命令行入口
```

数据流：

```
sample_project/ ──specs.py / code.py──▶ 规范化的规则、符号、用例
        │                                      │
        │                                 store.py（写）
        │                                      ▼
        │                            seekdb / OceanBase
        │                                      │
        └──任务描述──▶ retrieval.py（读，SQL 检索）──▶ ContextBundle
                                                       │
                                     agent.py ────────┤
                                          │            │
                                   MiniMax 生成 diff     │
                                          │            ▼
                                     应用到工程     state.py（写/读任务状态）
                                                       │
                                                 PowerContext
```

## 二、数据库表结构

| 表 | 作用 | 主键 |
| --- | --- | --- |
| `spec_rule` | 规格规则正文与来源文件 | `(spec_id, rule_id)` |
| `code_symbol` | 类、函数、方法的签名与源码片段 | `(symbol, file)` |
| `test_case` | 测试用例及其直接覆盖的符号 `targets` | `(name, file)` |
| `import_batch` | 每次导入的批次、来源目录与资料数量 | `batch_id` |
| `import_record` | 每条资料在某个批次中的变更类型（inserted/updated/unchanged/removed） | `(batch_id, material_kind, material_key)` |

`content_hash` 记录资料内容指纹：导入时与库内指纹比较，得到 inserted / updated /
unchanged，规格更新后重新导入即为 updated，检索结果随之改变。工程里已经删除的
资料在重新导入时会被清除（removed），库内内容始终等于当前工程的内容。

## 三、上下文组装算法

1. **切词**：`tokenize()` 把任务描述切成检索词。英文按单词（长度 ≥ 2），中文按
   二元组，长度 ≤ 6 的中文串额外保留整体，最多 28 个词。
2. **规格**：
   - `spec_ids_in()` 用正则 `^[A-Z][A-Z0-9]*-[A-Z0-9]+-\d{2,4}$` 取出任务显式引用的
     规格编号，调用 `search_spec_rules([], spec_ids=[...])` 取回该规格的全部规则；
   - 再用关键词调用 `search_spec_rules(tokens)`，得分为命中词数，低于 2 分的规则丢弃。
3. **代码**：`identifiers_in()` 从规格正文与任务描述中取出标识符候选，
   `search_code_symbols(tokens, names=[...])` 按名称（`symbol = %s OR symbol LIKE %s`）
   或关键词检索；任务描述中直接点名的符号归入「需要修改的代码」。
4. **测试**：导入阶段为每个测试用例记录 `targets`（它在 AST 中直接引用的代码符号）。
   目标测试 = `targets` 与「需要修改的代码」有交集的用例；回归测试 = 其余用例。

三个检索接口都是真实 SQL，命中词数用 `CASE WHEN ... LIKE %s THEN 1 ELSE 0 END`
在 SQL 内累加，过滤条件用 `OR` 连接，保证「命中任意一个词即可召回」。

## 四、任务状态

状态是一个 JSON 对象，字段包括 `task_id`、`title`、`description`、`stage`、
`specs`、`code`、`target_tests`、`regression_tests`、`done`、`todo`、
`verification`、`context_path`、`updated_at`。

- `sddctx/state.py::LocalStateStore` 保存工作副本到 `.data/state/<task_id>.json`；
- `sddctx/state.py::PowerContextStateStore` 通过
  `POST /v1/memory/remember`（`kind=working_note`）写入，通过
  `POST /v1/memory/entries/list` 读回；写入文本首行是标记行
  `sddctx.task_state task_id=... stage=...`，其后是 JSON 正文，读回时按 `task_id`
  过滤并取 `updated_at` 最新的一条。

`stage` 取值：`created` → `context_ready` → `code_modified` → `verified`
（测试失败时为 `verification_failed`，模型调用失败时为 `agent_failed`）。

## 五、模块与 MiniMax 的交互

`agent.py` 把「任务描述 + 适用规格全文 + 待改文件当前内容 + 目标测试代码」拼成
提示词，要求模型只输出一个可用 `git apply -p1` 应用的 unified diff。应用前先用
`git apply --check` 校验，失败时退回 `--unidiff-zero`；两种都失败则记录错误，不
修改工程文件。请求与响应原文保存在 `.data/agent/<task_id>/`。

模型回复有两种来源，走同一条后续流水线：

| 来源 | 入口 | 记录字段 |
| --- | --- | --- |
| MiniMax 开放接口 | `python -m sddctx agent --task-id T-001`（需 `MINIMAX_API_KEY`） | `response.json` 为接口原始响应 |
| 助手会话产出 | `python -m sddctx agent --task-id T-001 --from-file <回复文件>` | `response.json` 的 `integration` 为 `assistant-session` |

补丁文件以 LF 换行写出，避免与仓库中 LF 结尾的源文件不匹配。
