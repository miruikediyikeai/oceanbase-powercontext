# 实际运行证据

验证日期：2026-11-06。所有 passed 项来自实际执行。JSON 为脚本记录的真实结果；没有模拟 Dify API、伪造节点状态或使用适配器代替 Dify 执行规则。

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 交付 DSL 重新导入、绑定 secret / 服务地址、发布 | 通过 | `dify-import.json`：真实 Dify 1.9.1 控制台 API、DSL 0.4.0、9 个节点、文件哈希 |
| 完整玩法、跨 Dify 用户回访、配套 `/play` 入口 | 8 次真实 Dify 运行通过 | `dify-verification.json`：逐轮 run ID、正文、记忆版本及 citation |
| 九个节点逐轮执行 | 72 条全部 succeeded | 同上 `node_traces`：控制台 API 实际读回，保留节点 ID / 类型 / 状态 / 耗时 |
| 部署容器重启后持久化与回访 | 另 4 次真实 Dify 运行通过 | `deployed-restart.json`：先寄存 / 约定 / 结案，实际重启 memory 与 adapter，再用新 Dify 用户开门 |
| 重启前后状态一致、原始 PowerContext API 读回 | 通过 | 同上 `before_restart == after_restart`，含 state / version / scope / citation；`powercontext_direct_readback` 与存档匹配 |
| 镜像构建及独立 Compose 部署 | 9 个容器运行，依赖检查通过 | `deployment.json`：实际镜像 ID / digest、Linux amd64、包版本、容器状态、`pip check` |
| 原生真实 PowerContext 十轮交互与故障 / 一致性边界 | 通过 | `powercontext-verification.json`：隔离、版本冲突、提交幂等、进程重启及离线 503；明确不是 Dify 引擎证据 |
| 规则边界与模型失败契约测试 | 12 项通过 | `tests.txt`；模型 HTTP 响应为测试合成，不代表真实模型调用 |
| 作品 HTML 返回和服务密钥不出现在 HTML | 通过 | `dify-verification.json` 的 HTTP 检查；原有 `web-http-smoke.json` 另记录未配置 Dify 时的错误提示 |
| 浏览器渲染 / 点击、可选 Dify studio profile | 未验证 | 当前证据覆盖 HTML HTTP 返回与后端入口，未交付浏览器截图 |
| 可选真实 AI 润色 | 未配置 / 未验证 | 按用户要求交付无密钥规则模式，未声称调用真实模型 |

## PowerContext 如何影响玩法

`dify-verification.json` 的寄存回合保存“会打嗝的月亮 / 每周五变成奶酪”，后续错误证词被驳回，含“奶酪”的证词通过；结案后换 Dify API 用户，仍引用“咕叽”和旧纪念物。每轮的 `memory_citation` 来自真实 PowerContext entry version。

`deployed-restart.json` 在保留命名卷的前提下实际重启两个配套容器，先核对完整状态、应用版本号和精确引用不变，再通过真实 PowerContext `entries/list` 读回并执行 Dify 回访。记忆参与后续业务结果，且不依赖进程内缓存或仅检查连接成功。

## 复现

按 [独立本地环境](../docs/LOCAL_DIFY.md) 从新配置启动、导入、发布，然后运行：

```bash
python scripts/verify_dify.py --console-traces --web-url http://127.0.0.1:8812
python scripts/verify_deployed_restart.py
python scripts/record_deployment.py
```

Windows 上调用 WSL 内 Docker 时，为后两条加 `--wsl-distro Ubuntu-22.04`。所有脚本生成新随机通行证，初始存档为空，不删除已有玩家数据。结果写入 Git 忽略的 `.runtime/`；审核后再复制公开证据。

实际构建使用 Docker Engine / Compose（版本见 deployment.json）、`python:3.13.9-slim`、固定约束与 HTTPS 清华 PyPI 镜像。WSL 自定义内核的模块加载、会话保活和重建网络步骤已写入运行说明，不依赖未交付的本地配置。Dify 官方工作室网页是可选 profile；默认作品入口为 `http://127.0.0.1:8812`。

## 脱敏范围

公开材料保留自编示例文本、记忆 UUID / citation、Dify run / app ID 和节点状态；不包含服务密钥、应用 API Key、管理员 token / 密码或玩家通行证。节点原始 inputs / process_data / 请求头未导出，避免泄露 HTTP Authorization。DSL 哈希按 UTF-8 / LF 计算，与导入 API 实际接收的文本一致，可跨 Windows / Linux 核对。原始数据库、`.env` 和虚拟环境不提交。
