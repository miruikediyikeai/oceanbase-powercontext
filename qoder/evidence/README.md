# 实际执行证据

以下文件来自本地实际运行，使用项目自编样例，不含真实用户效果数据。

| 文件 | 内容 |
| --- | --- |
| `verification.json` | 真实 PowerContext 1.2.0 与应用 HTTP 进程执行的 8 项检查；包含双进程重启恢复、精确引用读回、隔离和不可用处理 |
| `browser-verification.json` | Microsoft Edge / Playwright 浏览器检查结果；桌面 1440×1000、手机 390×844 |
| `browser-memory.json` | 浏览器“导出记忆证据”按钮下载的实际记录，含 6 张卡片与 1 条偏好 |
| `environment-windows-py313.txt` | 执行环境的 Python 依赖清单；含 Windows 专用包，不应直接用作 Linux 安装清单 |
| `memory.png` | 应用“学习记忆”页面的实际截图 |
| `mobile.png` | 手机宽度下的实际复习页面截图 |
| `site.png` | 静态项目介绍页的本地浏览器截图，不代表 Qoder Sites 发布成功 |
| `../site/preview.png` | 桌面工作区实际截图，也供介绍页展示 |

记录中的“验证学习者”“小林”是样例称呼。Scope ID 和 citation 由实际 PowerContext 服务产生，不是账号凭据。独立验证使用的临时数据库在结束后移除；引用用于说明本次执行结果，重跑脚本会产生新的真实引用。

复现实际服务验证：在 `qoder/` 中运行 `python verify.py --output .data/verification.json`。复现浏览器操作：按 README 的“验证样例”执行。报告中的浏览器时间采用 UTC，界面按浏览器所在时区显示复习时间。

复习规则测试共 7 项，通过 `python -m pytest -q` 执行。代码检查使用 `ruff check .` 和 `ruff format --check .`。
