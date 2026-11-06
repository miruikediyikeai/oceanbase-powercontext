# 实际运行证据与验收缺口

检查日期：2026-11-06，Asia/Shanghai。所有 passed 项均来自实际执行；没有生成、改写或伪造 Dify 引擎执行记录。

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 真实 PowerContext 1.2.0 HTTP 读写 | 通过 | `powercontext-verification.json` 的逐轮 citation 和直接 API 读回 |
| 十轮连续玩法，按旧来历认领和按旧约定接梗 | 通过 | 同上 `rounds`，包含失败与成功反馈 |
| 两个进程重启后持久记忆、版本与引用恢复 | 通过 | 同上 `before_restart` 与 `after_restart` 完全一致 |
| 其他通行证隔离、版本冲突、提交幂等、离线503 | 通过 | 同上 `checks`；脚本中为真实 HTTP 断言 |
| 规则边界与模型失败契约测试 | 12 项通过 | `tests.txt`；模型 HTTP 响应为测试合成，不是真实模型调用 |
| 网页 HTML 实际返回和未配置 Dify 的错误提示 | 通过 | `web-http-smoke.json`，仅 HTTP 检查 |
| 浏览器页面渲染与交互 | 未验证 | 截图命令被自动审批策略阻止，未交付截图 |
| 真实 Dify 引擎导入、节点运行、发布后完整体验 | **未验证** | WSL 所在 D 盘空间耗尽，启动失败；应运行 `scripts/verify_dify.py` 补齐 |
| 配套 Compose 容器构建与启动 | 未验证 | 同一 WSL 环境限制；当前真实验证使用原生 Windows Python |
| 真实 AI 模型润色 | 未配置 / 未验证 | 按用户选项先交付无需密钥版本；不声称模型调用成功 |

## 环境限制的实际经过

本机最初存在可运行的 WSL2 Ubuntu 22.04，约 12 GB RAM。开发过程中下载了 Dify API 1.9.1、sandbox 0.2.12、PostgreSQL 和 Redis 镜像；下载 Dify Web 时 Docker 返回 `input/output error`。随后确认 Ubuntu 的 VHD 位于 `D:\WSL\Ubuntu-22.04`，D 盘可用空间仅约 2 MB，WSL 启动返回 `Wsl/Service/CreateInstance/E_FAIL`。未删除用户的其他文件，未重建或注销用户发行版。

已成功下载的公开镜像摘要（只证明镜像获取，不证明服务运行）：

- `langgenius/dify-api:1.9.1`：`sha256:c38936ee0149f8fabfb5f02ac716c545aa758ceef78c2369c733a86f8713aad4`
- `langgenius/dify-sandbox:0.2.12`：`sha256:4e65e8a351a2b17d11750b65403527a347e5cb938bc2271521ca7d689c008ad3`

真实 PowerContext 与适配器在 Windows CPython 3.13.9 上重新完成验证，使用独立目录和空闲端口；验证结束后已终止脚本自身创建的服务。JSON 不包含服务密钥、通行证或用户已有存档。运行目录和原始数据库不提交，复现由脚本自动生成。

## 补齐最终 Dify 验收

释放 WSL 所在分区空间，或换用可用的 Dify 1.9.1 环境，按 `docs/RUNBOOK.md` 重新导入、绑定环境变量并发布应用，然后运行：

```powershell
.\.venv\Scripts\python.exe scripts/verify_dify.py --output .runtime/dify-verification.json
```

全部成功后再将生成的真实 JSON 审核、脱敏并放入证据目录；更新本表中的未验证状态。当前交付不能声称已经通过完整的赛事基础验收。
