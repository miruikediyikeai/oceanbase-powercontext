# 拾忆 Recall

一个为个人学习设计的复习工作区。把课程知识整理成带出处的卡片，先回忆、再看答案、最后自评；下一次打开时，从 PowerContext 中读取已有进度和偏好，继续复习。

## 功能与技术分工

- 导入 JSON 知识卡片，内置 6 张数据库与上下文工程示例卡片。
- 根据用户自评安排下一次复习：未记住为 10 分钟后，模糊为 1 天后，记住为 1 天起逐次翻倍、最多 60 天。
- 优先安排已到期的薄弱卡片，每轮数量使用持久保存的用户偏好。
- 在“学习记忆”页面查看和导出 PowerContext 的实际记录、版本和精确引用。
- 重复导入相同卡片会跳过；相同 ID 内容不同则明确拒绝。过期页面提交复习或偏好会返回冲突，避免覆盖新进度。

PowerContext 1.2.0 是学习状态的唯一持久化服务。卡片、复习次数、薄弱点、下次时间和偏好都通过其 HTTP Memory API 读写。SQLite 是 PowerContext 自己的存储后端，应用不另建学习状态数据库。首版不使用 seekdb，也不调用语言模型；复习安排是可解释的启发式规则，不提供自动判分或语义检索。

## 环境与安装

需要 Python 3.11–3.13、可安装 PyPI 包的网络和浏览器。实际验证环境为 Windows、Python 3.13；PowerContext 上游把 Windows 支持标记为 experimental。所有命令从本目录 `qoder/` 执行。不要从生态赛道仓库根目录安装 PowerContext：那个分支仅存放各赛道作品，不包含上游 Python 包。

### Windows PowerShell

```powershell
cd D:\oceanbase-qoder\qoder
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

终端一：启动真实 PowerContext，默认监听 `127.0.0.1:8000`。

```powershell
.\.venv\Scripts\python.exe serve_powercontext.py
```

终端二：启动应用。

```powershell
$env:POWERCONTEXT_URL = 'http://127.0.0.1:8000'
$env:RECALL_LEARNER = 'demo'
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8080
```

打开 [本地工作区](http://127.0.0.1:8080)。如果你的仓库路径不同，替换第一行路径即可。

### macOS / Linux

```bash
cd /path/to/repository/qoder
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python serve_powercontext.py
```

另一个终端：

```bash
cd /path/to/repository/qoder
POWERCONTEXT_URL=http://127.0.0.1:8000 RECALL_LEARNER=demo \
  .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8080
```

以上是跨平台运行方式；本仓库的执行证据以 `evidence/verification.json` 记录的实际环境为准。

### 配置

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_URL` | `http://127.0.0.1:8000` | 后端服务地址 |
| `POWERCONTEXT_TOKEN` | 不设置 | 连接已有服务时使用的 Bearer token，仅保存在后端环境变量中 |
| `RECALL_LEARNER` | `demo` | 服务端选定的稳定学习者标识；变更后进入另一个 Scope |
| `serve_powercontext.py --data` | `.data/powercontext` | PowerContext 数据目录；重启时保留该目录 |
| `serve_powercontext.py --port` | `8000` | 本地 PowerContext 监听端口 |

该应用是单进程、单学习者的本地原型，不含账户系统。`RECALL_LEARNER` 用于稳定分区，不是身份认证。公开项目介绍页与个人学习后端分别部署；Qoder Sites 发布介绍页不会自动托管 Python 后端。

## 验证样例

1. 初始状态：使用新的 PowerContext 数据目录及 `RECALL_LEARNER=demo`，首页卡片数为 0。
2. 点击“导入示例，开始复习”，应出现 6 张卡片，点击卡片查看答案。
3. 选择“还没记住”，该卡复习次数变为 1、遗忘次数变为 1，下次时间为约 10 分钟后；当前计划排除这张尚未到期的卡。
4. 在“复习偏好”中把称呼设置为“小林”，每轮卡数设置为 2。返回首页，本轮计划应最多显示 2 张。
5. 打开“学习记忆”，查看 `recall_card_v1` 与 `recall_preferences_v1` 的原始记录、引用和版本；导出证据。
6. 关闭两个服务，再用同样命令和数据目录重启。称呼、复习状态和下次时间应保持不变。该卡到期后会因“上次未掌握”优先进入计划。
7. 再次导入示例卡片应提示跳过 6 张，不重置进度。打开两个浏览器标签，分别提交同一卡片：后提交的过期版本应提示刷新。

自动验证会启动独立的实际 PowerContext 与应用 HTTP 进程，完成导入、自评、偏好复用、精确引用读取、冲突处理，并重启两个进程核对持久性。验证使用临时数据目录，不会改动你的个人学习数据。

```powershell
.\.venv\Scripts\python.exe verify.py --output .data/verification.json
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
```

macOS / Linux 将 Python 路径替换为 `.venv/bin/python`。预期验证结果为 `status: passed`；退出码非零表示失败。`tests/` 覆盖复习时间、间隔上限、薄弱点排序和计划数量。自动验证是合成样例，不能作为真实用户数或提效数据。

## 项目结构

```text
qoder/
  app.py                    应用 HTTP 边界、输入校验和状态更新
  domain.py                 知识卡片模型与复习规则
  powercontext_gateway.py   PowerContext HTTP 适配
  serve_powercontext.py     本地真实 PowerContext 启动器
  verify.py                 实际服务验证与进程重启检查
  static/                   浏览器工作区
  samples/                  可复现初始数据
  tests/                    核心行为测试
  site/                     准备供 Qoder Sites 使用的介绍页资产
  docs/                     架构、Qoder 任务书与提交材料说明
  evidence/                 真实执行结果与截图
```

## 规模与兼容边界

单个学习空间上限 100 张卡片。每次读取和修改会读取该 Scope 的当前 Memory 列表；成本随卡片数增长，PowerContext 维护历史版本也会消耗存储。此原型没有做规模压测，不声明适合大规模知识库。未知学习数据版本、重复身份或损坏记录会阻止更新，避免静默覆盖。卡片内容不支持原地编辑；使用新 ID 导入更正后的内容。

复习写入没有自动重试。连接超时时写入可能已经完成，应刷新核对；过期版本会拒绝重复复习。批量导入可能部分成功，重新导入时跳过已存在的相同卡片。避免多个应用进程同时写入同一个学习空间。

## 赛道交付状态

这是 Codex 实现的可运行原型。当前材料不声称项目主要由 Qoder 开发，也不声称已通过 Qoder Sites 发布。后续 Qoder 功能开发任务、证据要求和站点发布步骤见 [Qoder 开发任务书](docs/QODER_TASKS.md) 与 [提交材料](docs/SUBMISSION.md)。赛题要求主要开发在 Qoder 中完成，单纯在 Qoder 打开仓库或发布网站不足以证明符合要求。

## 来源

- [PowerContext](https://github.com/oceanbase/powercontext)：使用 PyPI 1.2.0；HTTP 接入和本地服务配置参考上游公开文档及 `examples/jupyter/_tutorial.py`。本项目新增学习领域模型、API、工作区、验证脚本与介绍页，未修改 PowerContext 内部实现。
- 样例卡片为项目自编说明性数据，不包含外部论文或用户个人资料。
- 前端使用 HTML、CSS 与 JavaScript，无远程脚本依赖。
