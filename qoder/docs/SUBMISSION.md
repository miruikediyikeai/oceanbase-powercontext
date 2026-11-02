# 提交材料与验收入口

## 已提供的项目资产

| 材料 | 位置 | 核验方式 |
| --- | --- | --- |
| 应用源码 | `qoder/*.py`、`qoder/static/` | 按 README 启动 |
| 运行说明 | `qoder/README.md` | 安装依赖，运行两个服务 |
| 初始数据 | `qoder/samples/database-basics.json` | 导入后得到 6 张卡片 |
| 验证脚本 | `qoder/verify.py` | 运行后输出 passed 及实际服务证据 |
| 行为测试 | `qoder/tests/` | `python -m pytest` |
| 架构说明 | `qoder/docs/ARCHITECTURE.md` | 核对 Memory 如何影响复习计划 |
| 网站源文件 | `qoder/site/` | 交给 Qoder Sites 创建并发布 |

## 仍需实际完成的参赛材料

| 材料 | 当前状态 | 完成方式 |
| --- | --- | --- |
| Qoder 主要开发记录 | 尚未提供 | 在 Qoder 执行真实主要功能开发，保留对话和测试截图/录屏 |
| Qoder 账号 UID | 尚未提供 | 从实际个人账号中获取 |
| Qoder Sites 完整网址 | 尚未发布 | 用个人账号创建和发布，设为任何人可访问 |
| 无痕访问验证 | 尚未执行 | 未登录状态打开正式 Sites URL |
| 提交 commit hash | 尚未提交 | 将项目提交到指定基线后代分支后填写真实 hash |
| 真实用户效果 | 尚未收集 | 自愿试用、脱敏反馈与原始统计；不以合成验证代替 |

## 平台填写内容

- 项目名称：拾忆 Recall。
- 项目简介：通过带来源的知识卡片、自评驱动的间隔复习和跨次学习记忆，帮助个人接续复习进度。
- 指定技术：PowerContext 1.2.0；保存知识卡片、学习状态和偏好，后续计划读取并复用。
- 代码路径：仓库 `qoder/` 目录。
- 运行和复现：`qoder/README.md`。
- 网站、仓库最终分支/commit、Qoder UID：以真实发布与提交结果填写，不使用占位地址。

项目当前开发来源为 Codex 原型。此状态不满足“主要开发在 Qoder 中完成”的证明要求；最终材料须反映后续真实 Qoder 开发过程。
