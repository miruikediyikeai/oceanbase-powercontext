# 提交材料与验收入口

## 已提供的项目资产

| 材料 | 位置 | 核验方式 |
| --- | --- | --- |
| 应用源码 | `qoder/*.py`、`qoder/static/` | 按 README 启动 |
| 运行说明 | `qoder/README.md` | 安装依赖，运行两个服务 |
| 初始数据 | `qoder/samples/database-basics.json` | 导入后得到 6 张卡片 |
| 验证脚本 | `qoder/verify.py` | 运行后输出 passed 及实际服务证据 |
| 行为测试 | `qoder/tests/` | `python -m pytest`（14 项） |
| 浏览器级证据 | `qoder/evidence/browser-verification.json`、`restart-demo.json`、`notes-flow.png` | 核对实际界面流程与重启恢复 |
| 架构说明 | `qoder/docs/ARCHITECTURE.md` | 核对 Memory 如何影响复习计划与历史 |
| 网站源文件 | `qoder/site/` | 交给 Qoder Sites 创建并发布 |
| 开发任务书 | `qoder/docs/QODER_TASKS.md` | 对应 Qoder 中的实际开发任务 |

## 开发来源说明

- 可运行原型（JSON 导入、间隔复习、偏好与记忆复用、验证脚本）由 Codex 在生态赛道仓库完成，未修改 PowerContext 内部实现。
- 笔记转卡片（Markdown 分段、稳定 ID、可选模型候选、未确认不入库）与单卡复习历史/周汇总（历史版本读取、精确引用、UTC 统计规则、空状态）在 Qoder 中开发，并完成单元测试、真实 PowerContext 验证与浏览器级检查。
- 赛题要求主要开发在 Qoder 中完成；以上划分只陈述事实，不以任务书本身当作开发证据。真实执行证据见 `qoder/evidence/`。

## 仍需实际完成的参赛材料

| 材料 | 当前状态 | 完成方式 |
| --- | --- | --- |
| Qoder 主要开发记录 | 截图已保存 | `evidence/qoder-dev-01.png`、`qoder-dev-02.png`（Qoder 应用内开发对话实拍，已核对不含凭据）；录屏如需再补 |
| Qoder 账号 UID | 尚未提供 | 从实际个人账号中获取 |
| Qoder Sites 完整网址 | 已发布 | https://shiyi-recall-zoprrpyewzu.qoder.zone（可见范围：任何人可访问，已匿名核验 200） |
| 无痕访问验证 | 尚未执行 | 未登录状态打开正式 Sites URL |
| 提交 commit hash | 已推送 | `2026-competition-ecosystem` 分支（HEAD 以分支页为准）；因网络限制 git 智能协议传输被阻断（403），经 GitHub Git Database API 推送，提交日期被平台规范化为 UTC，分支内容与本地 `codex/qoder-studycoach`（主体提交 `3f0442e0`）完全一致但 SHA 不同 |
| 真实用户效果 | 尚未收集 | 自愿试用、脱敏反馈与原始统计；不以合成验证代替 |

## 平台填写内容

- 项目名称：拾忆 Recall。
- 项目简介：把笔记整理成带来源的知识卡片，通过自评驱动的间隔复习与 PowerContext 学习记忆接续进度，并提供可核对的单卡复习历史与周汇总。
- 指定技术：PowerContext 1.2.0；保存知识卡片、学习状态和偏好，历史版本驱动复习回顾。
- 代码路径：仓库 `qoder/` 目录。
- 运行和复现：`qoder/README.md`。
- 网站、仓库最终分支/commit、Qoder UID：网站为 https://shiyi-recall-zoprrpyewzu.qoder.zone；仓库为 `2026-competition-ecosystem` 分支（HEAD 以分支页为准）；Qoder UID 由本人从账号页面填写。
