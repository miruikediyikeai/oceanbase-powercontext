# 提交定位

目标仓库：`https://github.com/miruikediyikeai/oceanbase-powercontext`

分支：`2026-competition-ecosystem`

作品目录：`dify/`

入口材料：`dify/README.md`

DSL：`dify/dsl/strange-cabinet.yml`，唯一文件，导入后绑定 `cabinet_url` 和 `cabinet_token`。

版本：Dify 1.9.1 / DSL 0.4.0 / PowerContext 1.2.0。

在评测平台填写提交的实际 commit hash，可在推送后运行 `git rev-parse HEAD` 获取。该 commit 以指定官方赛事分支为祖先，仅增加对应 `dify/` 赛道资产。

完整运行证据位于 `dify/evidence/`：真实 Dify 重新导入与发布、8 次运行的 72 条成功节点记录、另 4 次真实 Dify 的容器重启持久化验证，以及实际镜像 / 包版本。默认无需模型密钥；AI 润色为可选项。复现推荐从 `dify/docs/LOCAL_DIFY.md` 开始。Git 推送不等同于评测平台已接收或验收通过。
