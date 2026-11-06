# 运行说明

## 版本与资源

| 依赖 | 版本 / 条件 |
| --- | --- |
| Dify | 官方 `langgenius/dify` tag `1.9.1`，源码 commit `cd47a47c3b4acc4d4457b73777722602c33bc950` |
| DSL | `0.4.0`，文件 `dsl/strange-cabinet.yml`；Workflow，9 个节点，无插件依赖 |
| PowerContext | PyPI `powercontext[server]==1.2.0`，自带 HTTP API 与 SQLite 持久层 |
| 配套服务 | Python 3.13；实际验证为 Windows / CPython 3.13.9 |
| Web 服务 | FastAPI 0.135.1、uvicorn 0.41.0、httpx 0.28.1 |
| 模型 | 默认无需；可选 OpenAI 兼容 Chat Completions 模型，不依赖 Embedding / Rerank |
| 资源 | 配套服务建议 2 GB 内存；完整 Dify 建议至少 8 GB 内存、15 GB 可用磁盘；具体以官方部署要求为准 |
| 网络 | 初次安装需要 PyPI / GitHub / 镜像仓库；运行时 Dify → 适配器 → PowerContext 必须可达；仅 AI 模式需要模型网络 |

`constraints.txt` 固定本次 Windows 实测的依赖解析版本，Windows 特有依赖带平台标记。Linux 容器配置已交付，尚未完成容器启动验证。不要在赛事仓库根目录运行 `pip install .`：赛事分支不含 PowerContext Python 包。

## 1. 安装和启动配套服务

所有命令在 `dify/` 执行。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

将生成的服务 token 填入 `.env` 的 `CABINET_TOKEN`。它只用于 Dify 调用适配器，不是玩家通行证，也不是 Dify API Key。

```powershell
.\.venv\Scripts\python.exe scripts/run_local.py
```

Linux / macOS 的 Python 方式：

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -c constraints.txt
cp .env.example .env
# 编辑 .env，设置 CABINET_TOKEN
.venv/bin/python scripts/run_local.py
```

启动器从 `.env` 读取配置，默认 PowerContext 在 `127.0.0.1:8811`、适配器在 `127.0.0.1:8812`。PowerContext 数据目录 `.data/memory/` 会自动初始化，不需要预置记忆、人工建表或导入素材。按 Ctrl+C 停止本次启动的两个服务；重新运行保留原记忆。

如果连接已有 PowerContext，不使用本地启动器，改为：

```powershell
# 先在环境变量中设置 CABINET_TOKEN、POWERCONTEXT_URL，必要时设置 POWERCONTEXT_TOKEN
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8812 --workers 1
```

`/health` 只有能访问真实 PowerContext 时才成功；API 文档在 `/docs`。本地 PowerContext 默认禁用认证，只监听环回地址；容器中的 memory 不发布宿主机端口。

可选容器方式（Docker Compose v2）：

```bash
cp .env.example .env
# 填写 CABINET_TOKEN
docker compose up --build -d
docker compose logs --tail 100
```

命名卷 `memory-data` 保存真实记忆。普通 `docker compose down` 保留数据；不要使用 `down -v` 清理仍需使用的存档。

## 2. 准备 Dify 1.9.1

已有该版本 Dify 时直接复用。否则按官方仓库该 tag 的 Docker 部署说明操作。请将运行环境放在赛事仓库外：

```bash
git clone --depth 1 --branch 1.9.1 https://github.com/langgenius/dify.git dify-runtime
cd dify-runtime/docker
cp .env.example .env
docker compose up -d
```

浏览器打开部署环境对应的 Dify `/install` 页面完成初始化。应先确认磁盘实际位于哪个分区，特别是 WSL 的 VHD 所在分区，不要只检查工作目录所在盘。

本作品不需要为 Dify 配置模型供应商、知识库、Embedding、Rerank 或工具插件。官方默认部署里的 PostgreSQL 是 Dify 自身数据库；**不将其声明为 seekdb**。

## 3. 导入与网络配置

1. Dify 工作室 → 创建应用 → 导入 DSL，选择 `dsl/strange-cabinet.yml`。只导入这一个文件。
2. 打开工作流的环境变量面板，填写 `cabinet_url` 与 `cabinet_token`。`cabinet_token` 是 secret，值与配套服务 `.env` 的 `CABINET_TOKEN` 相同；导出的 DSL 不含真实值。
3. `cabinet_url` 必须从 **Dify API 服务容器内部** 可达；末尾不带 `/`。不能把容器自己的 `127.0.0.1` 误当宿主机。
4. 运行一次：输入随机私人通行证（8–64 位字母、数字、下划线或短横线）与 `开门`。检查 load、game、commit、confirm、narrate、end 的节点结果。
5. 按验证说明完成连续操作，再点击发布，使用 Dify 网页应用或应用 API。

| 部署关系 | `cabinet_url` 示例与配置 |
| --- | --- |
| Dify API 与适配器均为宿主机进程 | `http://127.0.0.1:8812` |
| Docker Desktop 中的 Dify → Windows 配套服务 | `http://host.docker.internal:8812`；必要时设置 `CABINET_BIND_HOST=0.0.0.0` 后重启适配器，并允许相应网络访问 |
| Linux Docker → 同一 Linux 宿主机服务 | API 服务配置 `extra_hosts: ["host.docker.internal:host-gateway"]`；适配器监听 `0.0.0.0`，DSL 使用 `http://host.docker.internal:8812` |
| 配套 Compose 与 Dify Compose 共用网络 | 可将适配器加入 Dify API 所在网络，使用 `http://adapter:8812`，不依赖宿主端口 |
| 两套 Compose 分开、经 Linux 宿主机连接 | 配套 `compose.yaml` 默认只向 `127.0.0.1` 发布；需为指定 Docker 网关地址调整端口绑定，不能只设置 host-gateway 就认为可达 |

Dify 自建环境若通过 SSRF 代理拦截内网 HTTP，请在管理员可控的测试网络中为**适配器的确切主机和端口**配置允许规则。先在 API 容器中检查 `GET <cabinet_url>/health`，再在 HTTP 节点中运行。不要为了通过测试关闭整个工作区的网络保护。

## 4. 可选体验页与 API

直接使用 Dify 网页即可，不需要额外前端。配套的纸质窗口页面只是另一种入口：

1. 在 Dify 应用“访问 API”页面创建应用 API Key。
2. `.env` 设置 `DIFY_APP_KEY`，以及从适配器可达的 `DIFY_API_URL`（形如 `http://127.0.0.1:5001/v1`，或反向代理的 `/v1`）。示例文件的 host.docker.internal 值用于容器方式，原生 Python 运行请改成本机实际地址。
3. 重启适配器，打开 `http://127.0.0.1:8812`。密钥只保留在后端，浏览器不会收到服务 token 或 Dify API Key。
4. 网页自动生成通行证并存入当前浏览器 localStorage，可复制通行证换浏览器继续；“换张通行证”创建新故事，不删除旧档案。

API 输入：

```json
{
  "inputs": {"passport": "replace-with-private-random-passport", "move": "开门"},
  "response_mode": "blocking",
  "user": "judge-session-a"
}
```

发送到 Dify `POST /v1/workflows/run`，Authorization 为该应用的 Bearer API Key。成功后 `data.outputs` 包含 `text`、`memory_version` 和 JSON 字符串 `memory_citation`。

## 5. 可选 AI 润色

在适配器环境中同时设置三项，再重启：

```dotenv
NARRATOR_BASE_URL=https://your-model-provider.example/v1
NARRATOR_MODEL=your-chat-model
NARRATOR_API_KEY=replace-with-your-key
```

接口为 `POST <base>/chat/completions`；需支持普通非流式 Chat Completions，返回 `choices[0].message.content`。模型自行从提供方获取，按实际提供方计费。本项目不绑定特定供应商或可用性。

模型只读取已提交的规则结果，生成一段附加旁白；不向模型提供通行证、服务 token 或 Dify API Key。模型不可用、响应格式错误或 15 秒超时，输出“AI 润色暂不可用，回合已保存”，不重写记忆、不重计分。默认缺少配置时直接显示“本回合未调用生成模型”。AI 文本本身可能不准确，应以原始规则回执为准。

## 6. 检查与已知限制

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c constraints.txt
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe scripts/verify.py
.\.venv\Scripts\python.exe scripts/verify_dify.py
```

前者使用独立临时服务和目录测试真实 PowerContext，不要求你的 `.env` 或 Dify；最后一条才调用真实已发布 Dify 引擎，需要 `.env` 的应用配置。

- 运行适配器必须使用 **一个 worker / 一个实例**；进程锁串行化读改写，PowerContext citation 补充版本检查。不支持多副本水平扩容。
- 通行证是持有即授权的存档凭据，不是账号系统。使用随机值，保密，不使用公开昵称作通行证。记忆 scope 之间不相互检索。
- 仅存结构化精确记忆，不声称向量语义检索或无限自然语言对话。业务前缀使用空格、物品名必须精确匹配，来历至少含两个文字或数字。
- 每次输入最多 200 字；物品名最多 28 字、来历和约定最多 100 字、证词最多 150 字。中文标点可以出现在内容中；用半角 `|` 分隔业务字段。
- 每条 PowerContext 记忆上限 8192 UTF-8 字节。超长拒绝并明确提示，不静默截断玩家状态。
- HTTP 提交以 Dify workflow_run_id 幂等，保留最近一次提交的请求 ID；同一个 HTTP 提交紧接着重试不会重复得分。更早的旧请求被版本检查拒绝；不宣称永久去重所有历史请求。点击“重新运行”会产生新的 run ID，应先 `看看` 核对状态。
- PowerContext 断开时不会编造存档。提交超时可能结果未知，重连后先 `看看`。已成功保存后旁白节点失败会返回原规则回执。
- Dify 自身可能记录输入及通行证，只在受控评测环境使用，分享执行截图前遮盖通行证与凭据。
- 当前机器 D 盘满导致 WSL 无法启动，真实 Dify 导入和容器启动尚未实测；该限制影响提交完成度，详见 evidence/README.md。
