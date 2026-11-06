# 怪奇失物招领处

**一间会记住你胡说八道的失物招领处。** 你把“会打嗝的月亮”交给夜班柜员阿纸，编一段来历，许下一句约定。下次换个会话来，阿纸会翻出原档案，拿你的旧梗接着说。

本作品位于赛事要求的 `2026-competition-ecosystem` 分支的 `dify/` 目录，选择 **Dify + PowerContext**。不依赖 seekdb、知识库、Embedding、Rerank 或自定义插件。

## 怎么玩

一局约三分钟。同一张私人通行证关联同一份持久记忆；第一次用随机通行证，输入 `开门`。

| 输入示例 | 窗口业务 |
| --- | --- |
| `寄存 会打嗝的月亮 | 每周五变成奶酪` | 你的脑洞成为真实档案，离谱值 +2 |
| `寄存 拒绝加班的影子 | 下班后替我去月球钓鱼` | 两件怪物品串成一场“连锁事故” |
| `约定 每次见面先说咕叽` | 柜员下一次开门会主动提醒 |
| `认领 会打嗝的月亮 | 奶酪是它的周五皮肤` | 证词匹配原来历至少两个连续文字或数字，认领成功 +3 |
| `结案` | 领取专属称号和收据，腾空柜子，留下纪念物暗号 |
| `开门` / `看看` | 翻出旧约定、上一班称号与暗号；跨 Dify 用户或会话仍有效 |

认领失败不加分，重复认领不加分。每班最多三件物品，结案得分达到 10 时获得“宇宙级烂摊子收藏家”，否则为“负一楼常驻编外人员”。结案相册保留最近三班；约定持续有效，新的约定会替换旧约定。

默认是**规则剧场**：无需模型密钥、输出可复核。可选配置 OpenAI 兼容模型，为已执行结果追加 AI 碎碎念。界面明确标注本回合是否实际调用模型；AI 不修改得分、记忆或认领结果。当前交付的实际运行证据来自规则模式，未声称完成真实模型调用。

## 技术实际做了什么

```mermaid
flowchart LR
    UI[可选体验页 / Dify 网页 / API] --> D[Dify Workflow]
    D --> R[HTTP 读取旧档案]
    R --> C[Dify Code：玩法、证词核对、结案]
    C --> W[HTTP 提交新状态]
    W --> N[可选 AI 旁白 / 规则模式标记]
    N --> O[回执：正文、记忆版本、精确引用]
    R <--> A[无游戏逻辑的 HTTP 适配器]
    W <--> A
    A <--> P[PowerContext HTTP Memory API]
    P --> S[PowerContext 自有 SQLite 持久层]
```

Dify 的 Code 节点内包含完整玩法源码，HTTP 节点负责读取和保存，保存确认后才输出成功回执。后端不提供脱离 Dify 的游戏执行接口。可选网页的 `/play` 入口转发到真实 Dify `/v1/workflows/run`，不会本地模拟工作流。

PowerContext 1.2.0 是**唯一游戏状态存储**。每张通行证以 SHA-256 派生独立 scope 的幂等键，通过 `remember` 创建状态、`entries/revise` 更新、`entries/list` 读回；精确 citation 随 Dify 输出交付。约定、物品来历、认领状态、得分和相册都从真实记录恢复。适配器没有备用本地存档，记忆服务断开会返回错误。

## 材料入口与验证状态

| 材料 | 用途 |
| --- | --- |
| [dsl/strange-cabinet.yml](dsl/strange-cabinet.yml) | 唯一 DSL；Dify 1.9.1、DSL 0.4.0；无需导入其他 DSL |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | 安装、网络、服务地址绑定、发布、模型配置、限制 |
| [docs/VERIFICATION.md](docs/VERIFICATION.md) | 初始状态、完整体验顺序、预期表现、重置方式 |
| [docs/ENGINEERING.md](docs/ENGINEERING.md) | 数据结构、一致性、来源与可追溯技术说明 |
| [evidence/README.md](evidence/README.md) | 实际已验证项和未完成项，避免混淆模拟与真实运行 |
| `game.py` / `scripts/build_dsl.py` | 核心规则源码及可重复生成 DSL 的工具 |
| `app.py` / `serve_memory.py` | 配套 HTTP 服务及真实 PowerContext 启动器 |
| `static/index.html` | 可选的单页体验窗口，通过 Dify API 游玩 |
| `compose.yaml` / `Dockerfile` | 配套服务容器配置；Dify 使用官方 1.9.1 部署 |
| `scripts/verify.py` | 真实记忆服务、HTTP 适配器、DSL 内规则源码及进程重启验证 |
| `scripts/verify_dify.py` | 对已发布的真实 Dify 应用执行完整样例，生成引擎运行证据 |

目前真实 PowerContext 与适配器的连续交互、隔离、并发冲突、重试、重启恢复和故障验证已通过，另有 12 项测试通过（8 项规则回归、4 项模型失败响应契约）。**真实 Dify 引擎导入与执行尚未验证**：本机 WSL 所在 D 盘空间耗尽，Docker 无法继续启动。完整说明见运行证据目录；不能将独立规则执行记录视为 Dify 节点执行记录。提供了实际引擎验证脚本，仍须在可用的 Dify 环境中完成验收。

## 快速启动配套服务

需要 Python 3.13（实际验证：Windows 3.13.9），在本目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints.txt
Copy-Item .env.example .env
# 在 .env 填写随机 CABINET_TOKEN；通行证和服务 token 不是同一种凭据。
.\.venv\Scripts\python.exe scripts/run_local.py
```

随后按 [运行说明](docs/RUNBOOK.md) 导入 DSL，在 Dify 中设置适配器地址和服务 token，运行、发布。Dify 自带界面即可体验；如使用配套网页，还需把已发布应用的 API Key 填入 `.env` 的 `DIFY_APP_KEY`，再重启配套服务。网页入口为 [本地窗口](http://127.0.0.1:8812)。

所有配置样例均为占位值；真实凭据、PowerContext 数据文件、虚拟环境和 Dify 上游临时检出目录均不提交。
