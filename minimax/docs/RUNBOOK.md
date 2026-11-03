# 运行说明

## 1. 环境与依赖

| 组件 | 版本 | 用途 |
| --- | --- | --- |
| Python | 3.13.9（模块代码兼容 3.11+） | 运行上下文模块与示例工程 |
| PowerContext | 1.2.0（`powercontext[server]`） | 任务状态读写（HTTP Memory API） |
| seekdb | `5.7.25-OceanBase seekdb-v1.4.0.0` | 工程资料与索引存储（MySQL 协议） |
| pymysql | 1.2.3 | 连接 seekdb／OceanBase |
| httpx | 0.28.1 | 调用 MiniMax 开放接口 |
| pytest | 9.2.0 | 运行示例工程的测试 |

安装：

```bash
cd minimax
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows
# .venv/bin/pip install -r requirements.txt         # Linux/macOS
```

## 2. 配置

全部通过环境变量配置，缺省值见 `sddctx/config.py`：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SDDCTX_DB_HOST` | `127.0.0.1` | seekdb／OceanBase 地址 |
| `SDDCTX_DB_PORT` | `10000` | SQL 端口 |
| `SDDCTX_DB_USER` | `root` | 账号 |
| `SDDCTX_DB_PASSWORD` | 空 | 密码（本机实例无密码；不要把密码写进仓库） |
| `SDDCTX_DB_NAME` | `sdd_context` | 资料库名，模块会自动创建 |
| `SDDCTX_POWERCONTEXT_URL` | `http://127.0.0.1:8811` | PowerContext 服务地址 |
| `SDDCTX_POWERCONTEXT_TOKEN` | 空 | 访问令牌（本地回环服务未启用鉴权时不需要） |
| `SDDCTX_POWERCONTEXT_SCOPE` | 空 | 指定已有 scope；留空则自动创建 `sdd-context` scope |
| `MINIMAX_API_KEY` | 空 | MiniMax 密钥，代码改动环节必需 |
| `MINIMAX_BASE_URL` | `https://api.minimaxi.com` | MiniMax 接口地址 |
| `MINIMAX_MODEL` | `MiniMax-M2` | 使用的模型名 |
| `SDDCTX_PROJECT_DIR` | `minimax/sample_project` | 示例业务工程目录 |
| `SDDCTX_DATA_DIR` | `minimax/.data` | 工作数据目录（上下文、状态、模型请求） |

示例（Windows PowerShell）：

```powershell
$env:SDDCTX_DB_HOST = "127.0.0.1"
$env:SDDCTX_DB_PORT = "10000"
$env:SDDCTX_DB_USER = "root"
$env:SDDCTX_POWERCONTEXT_URL = "http://127.0.0.1:8811"
$env:MINIMAX_API_KEY = "<你的 MiniMax 密钥>"
```

## 3. 初始化数据

### 3.1 启动 seekdb／OceanBase

本机使用的 seekdb 实例运行在 WSL2 中：

```bash
wsl -d Ubuntu-22.04 -- bash -lc 'cd /home/bit && ./start_seekdb_server.sh'
```

该实例监听 `0.0.0.0:10000`（MySQL 协议），运行目录 `/home/bit/seekdb-run/seekdb1`。
从 Windows 访问时需要使用 WSL 的 IP（`wsl -d Ubuntu-22.04 -- hostname -I`），
例如 `SDDCTX_DB_HOST=172.29.132.88`；在 WSL 内部运行时用 `127.0.0.1` 即可。

验证连接：

```bash
mysql --no-defaults --protocol=TCP --connect-timeout=10 \
  --host=127.0.0.1 --port=10000 --user=root --batch \
  --execute='SELECT VERSION() AS version, 1+1 AS sql_check;'
```

本实例的遥测 ID（取自运行目录 `run/telemetry.json` 的 `content.id`）：
`fb0a292a-08d2-8497-b153-c291b31f1e55`。

### 3.2 启动 PowerContext

```bash
cd minimax
.venv/Scripts/python.exe scripts/start_powercontext.py --port 8811 --data .data/powercontext
```

服务使用文件型 SQLite（`.data/powercontext/memory.db`），重启后数据仍在。

### 3.3 导入工程资料

```bash
.venv/Scripts/python.exe -m sddctx import
```

该命令会建库建表、解析 `sample_project/` 下的规格与代码、写入数据库并记录导入
批次；同时会删除工程里已经不存在的资料（规格规则、代码符号、测试用例），保证
数据库中的内容始终等于当前工程的内容。输出形如：

```json
{
  "batch_id": "batch-20261103102834",
  "specs": 9,
  "code_symbols": 9,
  "test_cases": 13,
  "removed": {"spec_rule": [], "code_symbol": [], "test_case": []},
  "counts": {"spec_rule": 9, "code_symbol": 9, "test_case": 13, "import_batch": 3}
}
```

## 4. 调用入口

模块的唯一入口是命令行 `python -m sddctx`（等价于 `python -m sddctx.cli`）。

| 命令 | 输入 | 输出 |
| --- | --- | --- |
| `import [--batch-id ID]` | 示例工程目录 | 导入摘要（资料条数、变更类型、库内统计） |
| `context --task-id T-001 --task "<任务描述>" [--title 标题]` | 任务编号与任务描述 | 上下文 JSON（`.data/context/T-001.json`）与可读摘要（`.data/context/T-001.md`），并写入任务状态 |
| `state save --task-id T-001 [--stage 阶段]` | 任务编号 | PowerContext 写入结果（scope、memory 版本、citation） |
| `state show --task-id T-001` | 任务编号 | 从 PowerContext 读回的任务状态 JSON |
| `agent --task-id T-001 [--files 相对路径 ...] [--from-file 模型回复文件]` | 任务编号 | MiniMax 调用结果与改动文件清单 |
| `verify --task-id T-001 [--label before\|after] [--only target\|regression\|both]` | 任务编号 | 测试命令、退出码、日志路径 |
| `doctor` | 无 | 数据库、PowerContext、MiniMax 配置的连通性检查 |
| `stats` | 无 | 数据库中的资料统计 |

最小调用示例：

```bash
.venv/Scripts/python.exe -m sddctx context \
  --task-id T-001 \
  --task "为 OrderRepository.list_orders 增加按订单状态筛选的能力，规格见 SPEC-ORD-003。"
```

### 4.1 MiniMax 的两种接入方式

| 方式 | 命令 | 说明 |
| --- | --- | --- |
| 开放接口（HTTP） | `python -m sddctx agent --task-id T-001` | 需要 `MINIMAX_API_KEY`；请求 `POST {base_url}/v1/text/chatcompletion_v2`，模型 `MiniMax-M2` |
| 会话产出 | `python -m sddctx agent --task-id T-001 --from-file <回复文件>` | 把助手会话给出的回复交给模块，按同一条流水线提取并应用 diff |

两种方式共用同一段代码：提示词组装（`build_user_prompt`）、diff 提取（`extract_diff`）、
`git apply` 应用（`apply_diff`）、证据落盘（`request.json` / `response.json` /
`change.diff`）。本次提交留痕的改动走的是会话产出方式，原因见
`docs/TECH_RECORD.md` 第 3 节。

## 5. 测试命令

示例业务工程自带 pytest 配置（`sample_project/pytest.ini` 已设置 `pythonpath = src`）：

```bash
cd minimax/sample_project
../.venv/Scripts/python.exe -m pytest tests -q
```

模块自身的端到端验证：

```bash
cd minimax
.venv/Scripts/python.exe scripts/run_verification.py          # 完整闭环（含 MiniMax 改动）
.venv/Scripts/python.exe scripts/run_verification.py --skip-agent  # 跳过模型改动步骤
.venv/Scripts/python.exe scripts/spec_update_demo.py          # 规格变更后上下文随之更新
.venv/Scripts/python.exe scripts/powercontext_restart_demo.py  # 服务重启后读回任务状态
.venv/Scripts/python.exe scripts/capture_db_evidence.py       # 采集数据库使用记录
```

`spec_update_demo.py` 使用独立资料库 `sdd_context_spec_demo`，演示新增的规格规则
不会留在正式资料库里。

## 6. 已知限制

- 上下文检索使用关键词切分与 SQL `LIKE` 匹配，没有引入向量检索；中文按二元组切词，
  语义相近但用词不同的任务可能召回不足。
- 规格解析要求规则以 `## <规则编号> <标题>` 二级标题组织，元信息用 `- key: value`
  行写在标题之后；不符合该结构的规格文件会被解析成没有规则的文档。
- 任务状态写在 Memory 的 `text` 字段（上限 8192 字节）；任务上下文很大时只保存
  引用与摘要，不保存完整规格正文。
- `git apply` 依赖 git 可执行文件；模型产出的 diff 若无法应用会被拒绝并记录原因，
  不会修改工程文件。
- 示例工程是单进程内存仓储，不含数据库持久化，聚焦于规格与代码的关联本身。
