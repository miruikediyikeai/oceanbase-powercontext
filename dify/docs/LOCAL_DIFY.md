# 独立本地 Dify 验收环境

本目录提供一套专用 Dify 1.9.1 安装，将游戏从 DSL 导入、环境变量绑定、发布直到 API 游玩连成可复现流程。已有 Dify 的用户仍可按 RUNBOOK 手动导入，无需另建环境。

## 前置条件

Docker Engine（支持 BuildKit）、Docker Compose >= 2.24.4（支持 `!reset`）、Python 3.13。完整环境建议至少 8 GB RAM、15 GB 可用磁盘。Docker 数据盘与 WSL 的 VHD 所在分区也必须有足够空间。先检查端口 5001、8812 未被已有服务占用。

以下命令在 `dify/` 执行。Python 命令用你的虚拟环境解释器替换；Windows 为 `.venv\Scripts\python.exe`，Linux 为 `.venv/bin/python`。

## 启动、导入、发布

1. 安装脚本依赖并生成本地随机配置：

```bash
python -m venv .venv
# 激活 .venv 后执行
python -m pip install -r requirements-tools.txt -c constraints.txt
python scripts/init_local_config.py
```

这里只安装宿主机导入 / 验证工具；真实 PowerContext 和适配器依赖由 Dockerfile 安装。配置写入 Git 忽略的 `.env`，包括专用数据库、Redis、沙箱、插件服务、适配器的随机凭据及本地管理员账号。不打印密钥，不提交该文件。已有 `.env` 时脚本拒绝覆盖，应保留原文件，参照 `.env.example` 手动补齐缺少的配置。

2. 启动配套服务与 Dify：

```bash
docker compose -f compose.yaml -f compose.dify.yaml up --build -d --wait
```

Dify API 监听本机 5001，游戏窗口监听本机 8812。PowerContext、数据库、Redis、沙箱和插件服务均不发布端口。可用 `docker compose -f compose.yaml -f compose.dify.yaml logs --tail 50` 检查服务；分享日志前检查是否含凭据或通行证。

3. 仅对这套专用的新 Dify 安装初始化、导入并发布：

```bash
python scripts/configure_dify.py --initialize
```

脚本走真实 Dify 控制台 HTTP API，导入仓库内原始 DSL，再绑定 `cabinet_url=http://adapter:8812` 与服务 secret，发布工作流并创建应用 API Key。应用 key 和 ID 只写入本地 `.env`，导入报告写入 `.runtime/dify-import.json`。已初始化的环境不再执行 setup；再次调用会导入一个新应用，不覆盖原应用。

已有 Dify 时不使用 `--initialize`，自行填入 `.env` 的管理员配置，并通过 `--base-url` 与 `--cabinet-url` 指定实际地址。若不希望提供管理员配置，可完全使用 RUNBOOK 中的手动导入方式，只填写应用 API Key 来验证。

4. 让适配器读取新建的应用 key：

```bash
docker compose -f compose.yaml -f compose.dify.yaml up -d --no-build adapter
```

浏览器打开 `http://127.0.0.1:8812`。输入 `开门`，按三步玩法游玩。`/play` 必须经过真实 Dify 引擎，不在适配器内运行规则源码。

## 生成真实证据

```bash
python scripts/verify_dify.py --console-traces --web-url http://127.0.0.1:8812
```

执行七轮完整样例，再通过网页后端入口额外执行一轮回访。最后使用控制台 API 逐次核对九个实际节点全部 succeeded。证据输出 `.runtime/dify-verification.json`，只保留节点 ID、类型、状态与耗时，排除可能包含服务 token 和玩家通行证的原始输入、请求头及 process_data。

验证环境中 PowerContext 的全部状态保存在 `memory-data` 命名卷。重启 `memory` 和 `adapter` 后，使用同一通行证 `开门`，应继续提起旧约定和纪念物；不依赖 Dify API `user` 保持不变。

进一步验证部署后的持久化（会短暂重启本作品的 `memory` / `adapter`，保留数据卷）：

```bash
python scripts/verify_deployed_restart.py
python scripts/record_deployment.py
```

Windows 上用 WSL 内 Docker 时，为这两条命令加 `--wsl-distro Ubuntu-22.04`（替换为实际发行版名）。重启脚本先通过真实 Dify 寄存、约定、结案，检查重启前后 state / version / scope / citation 完全一致，再直接读回 PowerContext，并以新 Dify 用户回访。环境记录脚本只导出容器状态、镜像 ID / digest、实际包版本及 DSL 哈希，不导出环境变量。输出分别为 `.runtime/deployed-restart.json`、`.runtime/deployment.json`。

## 可选 Dify 工作室界面

默认只启动 Dify 引擎与作品体验页。需要 Dify 官方工作室界面时：

```bash
docker compose -f compose.yaml -f compose.dify.yaml --profile studio up -d web
```

打开 `http://127.0.0.1:8080`，用本地 `.env` 的管理员账号登录。此 profile 使用 `dify-web:1.9.1`，浏览器从 8080 调用本机 5001 控制台 API；服务端 CORS 仅允许该来源。默认 API/游戏验收不依赖此可选界面。

## 特定 WSL 环境与证书

本次机器使用自定义 WSL 内核，启动时未自动加载 bridge / nft_compat 等模块，导致 Docker 网络创建失败或 DNS DNAT 规则失败。这不是游戏 DSL 的依赖；如遇相同症状，可在 **WSL root** 中先执行：

```bash
bash scripts/wsl_docker_prereqs.sh
```

脚本只加载本次启动需要的内核模块并开启 IPv4 转发，不改写已有 Docker daemon 配置。若失败提示模块不存在，应使用包含 Docker 所需网络模块的正常 Linux / WSL 内核。已有失败容器需重新启动，使 Docker 重新配置 DNS。正常 Docker 环境无需此步骤。

在 Windows 上运行时保留一个 WSL 终端会话，避免无用户会话时发行版自动停止；systemd / Docker 后台进程本身不保证 WSL 一直运行。WSL 重启后需重新执行上述模块准备。若已出现网络 endpoint 不存在错误，在准备完成后对本作品执行 `docker compose -f compose.yaml -f compose.dify.yaml down`（**不加 `-v`**）再 `up -d --no-build --wait`，重建网络并保留数据库与游戏记忆卷。

本机另有宿主机自定义 CA，镜像构建通过 BuildKit 临时 secret 提供 CA bundle，保持 TLS 校验且不将机器 CA 放进镜像或仓库：

```bash
docker build --network host --secret id=pip_ca_bundle,src=/path/to/trusted-ca-bundle.pem -t strange-cabinet-app:1.0.0 .
docker compose -f compose.yaml -f compose.dify.yaml up -d --no-build --wait
```

标准网络无需 secret。`--network host` 只用于本次宿主机禁用 Docker NAT 的构建环境；正常环境可省略。若启用可选 AI 润色，适配器还需要访问模型服务的网络与信任链；本次实测规则模式不依赖外网模型。

Dockerfile 默认使用官方 PyPI。若下载缓慢，可为构建选择可访问的 HTTPS 镜像，例如 `docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t strange-cabinet-app:1.0.0 .`；依赖仍按 constraints 固定版本，保持 TLS 校验。本次验证环境使用了该镜像。

## 代理与数据保留

Dify HTTP 节点使用提交的 Squid 允许规则：仅可访问 `adapter:8812`，不关闭整个工作区的 SSRF 保护。Dify 1.9.1 的官方 Code 执行器固定发送 `enable_network=true`，因此沙箱必须配置 `ENABLE_NETWORK=true`；沙箱 HTTP(S) 代理同样指向该 Squid，交付的 Code 源码只做本地 JSON / 规则运算。PostgreSQL 只承担 Dify 自身数据，不将其冒充 seekdb；玩家游戏状态由真实 PowerContext 独立保存。

容器内 PowerContext 启用 Bearer 认证，`POWERCONTEXT_TOKEN` 由初始化脚本随机生成；适配器用同一值访问记忆 API。已有配置未填写该值时，Compose 回退到 `CABINET_TOKEN`，不会以未认证方式绑定 `0.0.0.0`。独立栈全部通过服务名通信，清除了基础配置中的 `host-gateway` 映射，因此不依赖 Docker 默认 bridge 的网关地址。

停止：`docker compose -f compose.yaml -f compose.dify.yaml stop`。重新启动使用 `up -d --no-build`。命名卷会保留；不要对还需使用的环境执行 `down -v`。首次构建下载依赖，后续可复用已构建镜像和 BuildKit pip 缓存。
