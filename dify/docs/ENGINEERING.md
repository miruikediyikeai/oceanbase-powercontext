# 工程与来源说明

## 职责和导入结构

唯一 DSL 的线性流程为：

`start → pack → load → game → commit → confirm → narrate → render → end`

- `pack` Code 节点校验通行证并用 JSON 序列化请求，避免引号、换行或分隔符破坏 HTTP 请求体。
- `load` HTTP 节点调用适配器，适配器经 PowerContext HTTP API 读取真实 scope 与 entry。
- `game` Code 节点使用交付的 `game.py` 原文。可通过 `scripts/build_dsl.py` 重建，回归测试检查两者一致。
- `commit` HTTP 节点写入整个有界状态；`confirm` Code 节点确认 HTTP 200 才把结果送往输出。
- `narrate` HTTP 节点提供可选旁白。配置默认值错误策略，使旁白 HTTP 失败时仍由 `render` 显示已提交回执；不会重新执行业务。
- `end` 输出正文、应用记忆版本和 PowerContext 精确引用。

## 存储格式

一张通行证对应一个 scope。创建 scope 的 `idempotency_key` 为 `dify-cabinet-v1:` 加通行证 SHA-256；scope 标题只展示哈希前缀。哈希不是登录系统，强随机通行证和受控部署仍然必要。

scope 中有一个 `kind=cabinet_state` 的当前 entry：

```json
{
  "state": {
    "schema": 1,
    "turn": 0,
    "score": 0,
    "items": [],
    "vow": "",
    "album": [],
    "visits": 0
  },
  "version": 1,
  "request_id": "来自真实Dify的workflow_run_id",
  "result": "本轮已确认提交的原始规则回执"
}
```

`items` 中每件物品有 `name`、`origin` 和 `claimed`；`album` 中每张纪念卡有 `title` 和 `souvenir`。`version` 是应用版本号，从首次提交的 1 起递增，**不等同于** PowerContext citation 的 memory revision。PowerContext 负责不可变历史版本与引用，适配器只存取当前 projection。

首次创建 memory artifact 时使用 `POST /v1/memory/remember`，不传不存在的 artifact revision。之后通过 `entries/revise` 附带读回的 citation 更新，避免盲目覆盖。读取走 `entries/list`；scope 的创建幂等键持久化在 PowerContext 中，因此进程重启不依赖本地 scope 缓存。

## 一致性和失败语义

适配器单进程、单 worker，使用进程锁保护同一服务中的读取 / 版本检查 / 写入。提交需要 `expected_version` 与当前应用版本相符，冲突返回 409。状态和上次请求 ID 同写入一条 PowerContext entry；最近请求重试返回已提交结果，不再次计算得分。

本方案不支持多适配器副本争抢首次创建，也不承诺所有历史请求的永久去重。需要扩展时应增加分布式事务或独占写入者，并另行验证。

本地 PowerContext 使用它自己的 SQLite backend；适配器没有第二套存档。记忆离线时返回 503。提交 HTTP 超时存在“结果未知”的情况，应先读取当前状态；模型追加旁白失败不影响已保存结果。

state、result 合起来在写入前检查 8192 UTF-8 字节限制。不静默截断物品或约定；相册业务规则仅保留最近三班。服务 API token 与游戏通行证分开，浏览器只持有自己的通行证，Dify 应用 API Key 保留在服务端。

## 模型行为

可选 AI 请求使用系统消息约束“一句不超过60字的旁白，不新增物品、事实、得分或承诺”，用户消息中只有已保存规则回执。模型不能调用存档接口、修改 Code 节点结果或影响判分。该约束是提示规则，不是对模型准确性的保证；规则回执与“AI 柜员碎碎念”明确分开。旁白没有回写为可信事实。

## 来源、许可、版本

- 玩法、规则源码、DSL、HTTP 适配器、验证脚本和网页为本次作品编写；没有复用题目列举的公开娱乐 DSL。
- `serve_memory.py` 参考同仓 `qoder/serve_powercontext.py` 的 PowerContext 初始化方式，调整了目录、端口、服务用途和绑定参数。该来源是 **Apache-2.0**；本目录保留同一许可证，见 `LICENSE` 与 `NOTICE`。
- Dify 节点配置参考官方 1.9.1 源码中的 HTTP Request、Code、Start、End 实体与 workflow fixtures，未将其源码打包为本作品代码。
- PowerContext 1.2.0 从 PyPI 安装。官方接口说明：[PowerContext API](https://powercontext.oceanbase.io/zh/docs/develop/interfaces/)。
- Dify 官方部署入口：[langgenius/dify 1.9.1](https://github.com/langgenius/dify/tree/1.9.1/docker)。
- 所有示例物品和来历均为项目自编，不涉及外部知识库、素材下载、图片生成服务或数据授权。
- 开发基线：官方赛事分支 commit `d647275c308e09b7b623f322a26f5c4e85734fb0`；开始开发时目标仓库该分支为 `a7f5e3bc954684ea0b9f1ff660bfabfc0d915154`，已确认包含官方基线。所有作品资产仅新增于 `dify/`。
