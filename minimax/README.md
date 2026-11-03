# 规有所依 —— 面向软件持续演进的 SDD 上下文模块

本目录是 MiniMax × OceanBase 赛题的参赛作品。模块围绕**一次真实的开发任务**，
把「规格 → 代码 → 测试」三者关联起来，交给开发助手完成代码修改，并把任务进展
持久化到 PowerContext 以便跨进程复用。

一次完整闭环：

```
规格/代码/测试  ──导入──▶  seekdb / OceanBase
                              │
        开发任务 ──检索───────┴──▶ 上下文（适用规格 + 待改代码 + 验证测试）
                                        │
                                        ▼
                                   MiniMax 生成代码改动
                                        │
                                        ▼
                       目标测试 + 回归测试  ──记录──▶  PowerContext 任务状态
```

## 一、模块结构

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| 资料解析 | `sddctx/specs.py`、`sddctx/code.py` | 解析规格 Markdown 为规则；用 AST 抽取类、函数、方法与测试用例 |
| 资料存储 | `sddctx/store.py` | 在 seekdb／OceanBase 中建库建表、写入资料、记录导入批次、检索规格/代码/测试 |
| 上下文检索 | `sddctx/retrieval.py` | 任务描述切词 → SQL 检索 → 组装上下文（规格、待改代码、目标测试、回归测试） |
| 状态持久化 | `sddctx/state.py` | 通过 PowerContext HTTP Memory API 写入/读回任务状态 |
| 模型接入 | `sddctx/agent.py` | 调用 MiniMax 开放接口，把上下文交给模型，产出并应用 unified diff |
| 命令行 | `sddctx/cli.py` | `import` / `context` / `state` / `agent` / `verify` / `doctor` / `stats` |
| 示例业务工程 | `sample_project/` | 可运行、带测试的订单结算工程 `orderkit`，含 3 份规格与 13 条测试 |
| 验证脚本 | `scripts/` | 端到端验证、规格变更演示、服务重启后状态复用演示、数据库使用记录采集 |

## 二、上下文是怎么选出来的

1. **切词**：任务描述中的英文按单词切分，中文按二元组切分（例如「状态筛选」→
   `状态`、`态筛`、`筛选`）。
2. **规格**：先用正则取出任务里显式引用的规格编号（如 `SPEC-ORD-003`），把该规格
   的全部规则直接纳入；再用切出的关键词在 `spec_rule` 表上做 SQL `LIKE` 检索，
   命中词数作为得分，命中数低于阈值的规则被丢弃，避免无关规则污染上下文。
3. **代码**：把规格正文里出现的标识符（如 `OrderRepository`、`list_orders`）作为
   候选符号，在 `code_symbol` 表上按名称或关键词检索；任务描述里直接点名的符号
   归为「需要修改的代码」，其余归为「相关但未直接修改的代码」。
4. **测试**：测试用例在导入时就记录了它直接引用的代码符号（`targets`）。覆盖到
   「需要修改的代码」的用例是**目标功能测试**，其余是**回归测试**。

所有检索结果都来自数据库的真实查询，模块不内置任何固定答案；规格改动后重新
导入，上下文随之变化（见 `docs/VERIFICATION.md` 的规格变更一节）。

## 三、三项技术的分工

| 技术 | 在本次任务中承担的角色 | 实际接入方式与版本 |
| --- | --- | --- |
| MiniMax | 依据检索到的规格与测试，生成 `OrderRepository.list_orders` 的实现改动 | 助手会话（会话模型为 MiniMax）产出改动，模块经 `sddctx/agent.py` 的同一条流水线提取、应用并留痕；模块另实现了 MiniMax 开放接口通道 `POST /v1/text/chatcompletion_v2`（模型 `MiniMax-M2`），详见 `docs/TECH_RECORD.md` 第 3 节 |
| seekdb / OceanBase | 保存规格规则、代码符号、测试用例与导入批次；上下文检索的取数来源 | MySQL 协议直连，seekdb `5.7.25-OceanBase seekdb-v1.4.0.0`，库名 `sdd_context` |
| PowerContext | 保存任务状态（适用规格、待改代码、测试、已完成/待办、验证结果），模块重启后读回 | PowerContext HTTP Memory API `POST /v1/memory/remember`、`POST /v1/memory/entries/list`，版本见 `requirements.txt` |

## 四、仓库内材料

| 路径 | 内容 |
| --- | --- |
| `docs/RUNBOOK.md` | 运行说明：依赖、配置、初始化、调用入口、测试命令、已知限制 |
| `docs/VERIFICATION.md` | 验证样例：任务、有效规格、初始数据、操作步骤、预期结果、重置方法 |
| `docs/TECH_RECORD.md` | 技术使用记录：MiniMax 调用、数据库查询、PowerContext 写入与复用 |
| `docs/ARCHITECTURE.md` | 数据模型、表结构、上下文组装算法 |
| `evidence/` | 真实运行记录：数据库查询、模型请求与响应、测试日志、状态复用记录 |

## 五、快速开始

```bash
cd minimax
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows
# .venv/bin/pip install -r requirements.txt                              # Linux/macOS

export SDDCTX_DB_HOST=127.0.0.1 SDDCTX_DB_PORT=10000 SDDCTX_DB_USER=root
export MINIMAX_API_KEY=<你的 MiniMax 密钥>

python scripts/start_powercontext.py --port 8811 &   # 启动本地 PowerContext 服务
python scripts/run_verification.py                   # 端到端验证样例
```

完整说明见 `docs/RUNBOOK.md`。
