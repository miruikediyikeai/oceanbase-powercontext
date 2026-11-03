# 验证样例

本样例覆盖一次完整的开发任务闭环：导入资料 → 检索上下文 → 保存状态 → 修改前
测试失败 → MiniMax 完成代码修改 → 修改后测试通过 → 模块重启后读回状态。

## 1. 任务描述

**任务编号**：T-001
**任务标题**：为订单仓储增加按状态筛选订单的能力
**任务描述**：

> 为 OrderRepository.list_orders 增加按订单状态筛选的能力，支持状态枚举与状态字符串
> （忽略大小写），非法状态抛出 ValueError，无匹配时返回空列表。规格见 SPEC-ORD-003。

## 2. 有效规格

上下文必须包含下列规格条目及其来源文件：

| 规则 | 标题 | 来源文件 |
| --- | --- | --- |
| `SPEC-ORD-003-R1` | 筛选语义 | `sample_project/specs/SPEC-ORD-003-order-status-filter.md` |
| `SPEC-ORD-003-R2` | 状态取值 | `sample_project/specs/SPEC-ORD-003-order-status-filter.md` |
| `SPEC-ORD-003-R3` | 非法状态 | `sample_project/specs/SPEC-ORD-003-order-status-filter.md` |
| `SPEC-ORD-003-R4` | 空结果 | `sample_project/specs/SPEC-ORD-003-order-status-filter.md` |

其余规格（`SPEC-ORD-001`、`SPEC-ORD-002`）属于示例工程的其它能力，与本任务无关。

## 3. 必须出现在上下文中的代码与测试来源

| 类别 | 必须包含 | 说明 |
| --- | --- | --- |
| 需要修改的代码 | `src/orderkit/repository.py::OrderRepository.list_orders` | 任务要求修改的符号 |
| 需要修改的代码 | `src/orderkit/repository.py::OrderRepository` | 所属类 |
| 目标功能测试 | `tests/test_repository_filter.py` 的 6 条用例 | 覆盖状态筛选、枚举入参、大小写、非法状态、空结果 |
| 回归测试 | `tests/test_models.py`、`tests/test_pricing.py` 共 7 条用例 | 既有行为必须保持 |

## 4. 初始数据

- **数据库**：空库 `sdd_context`（模块自动建库建表）。执行 `import` 后应包含
  9 条规格规则、9 个代码符号、13 条测试用例。
- **PowerContext**：空 scope。执行 `context` 后应出现一条 `kind=working_note` 的
  任务状态记录。
- **示例工程**：`sample_project/` 中 `OrderRepository.list_orders` 尚不支持 `status`
  参数，`tests/test_repository_filter.py` 已存在且描述目标行为。

## 5. 操作步骤

```bash
cd minimax
export SDDCTX_DB_HOST=127.0.0.1 SDDCTX_DB_PORT=10000 SDDCTX_DB_USER=root
export SDDCTX_POWERCONTEXT_URL=http://127.0.0.1:8811
export MINIMAX_API_KEY=<你的 MiniMax 密钥>

# 0) 启动依赖服务
.venv/Scripts/python.exe scripts/start_powercontext.py --port 8811 --data .data/powercontext &

# 1) 导入工程资料
.venv/Scripts/python.exe -m sddctx import

# 2) 检索上下文并写入任务状态
.venv/Scripts/python.exe -m sddctx context --task-id T-001 \
  --task "为 OrderRepository.list_orders 增加按订单状态筛选的能力，支持状态枚举与状态字符串（忽略大小写），非法状态抛出 ValueError，无匹配时返回空列表。规格见 SPEC-ORD-003。"

# 3) 修改前运行目标测试（应失败）
.venv/Scripts/python.exe -m sddctx verify --task-id T-001 --label before

# 4) 由 MiniMax 完成代码改动
.venv/Scripts/python.exe -m sddctx agent --task-id T-001

# 5) 修改后运行目标测试与回归测试（应全部通过）
.venv/Scripts/python.exe -m sddctx verify --task-id T-001 --label after

# 6) 换一个进程读回状态
.venv/Scripts/python.exe -m sddctx state show --task-id T-001
```

也可以一次跑完：`python scripts/run_verification.py`（每一步的命令、退出码与输出
写入 `evidence/verification/<时间戳>/`）。

## 6. 预期结果

| 步骤 | 预期 |
| --- | --- |
| 1 import | 退出码 0；输出 `specs=9, code_symbols=9, test_cases=13` |
| 2 context | 退出码 0；`specs` 含 `SPEC-ORD-003::SPEC-ORD-003-R1..R4`；`code` 含 `src/orderkit/repository.py::OrderRepository.list_orders`；`target_tests` 为 `tests/test_repository_filter.py` 的 6 条用例；`regression_tests` 为 models/pricing 的 7 条用例 |
| 3 verify before | 目标测试退出码 1，失败原因为 `TypeError: OrderRepository.list_orders() got an unexpected keyword argument 'status'`（功能未实现）；回归测试退出码 0 |
| 4 agent | MiniMax 返回 unified diff 并被 `git apply` 应用；`changed_files` 含 `src/orderkit/repository.py` |
| 5 verify after | 目标测试与回归测试退出码均为 0；任务状态 `stage=verified` |
| 6 state show | 输出 `found=true`，`source=powercontext`，`done` 含导入、检索、改动、测试四步，`verification` 含两次测试的退出码 |

### 规格更新后上下文随之更新

```bash
# 在副本中新增一条规格规则后重新导入
.venv/Scripts/python.exe scripts/spec_update_demo.py
```

预期：对比文件 `evidence/spec-update/<时间戳>/comparison.json` 中
`added_rule_ids` 含 `SPEC-ORD-003-R5`，`updated` 为 `true`；更新后的上下文
（`.data/context/T-SPEC-UPDATE.json`）包含 R5 的正文，而更新前
（`.data/context/T-SPEC-BASE.json`）不包含。演示在独立资料库
`sdd_context_spec_demo` 中进行，演示用的 R5 不会留在正式资料库里。

## 7. 重置方法

```bash
# 清空任务状态（PowerContext）
rm -rf .data/powercontext        # 停掉服务后删除，重启服务即为空状态

# 清空工作数据与上下文
rm -rf .data/state .data/context .data/test-logs .data/agent

# 清空资料库
mysql --no-defaults --protocol=TCP --host=$SDDCTX_DB_HOST --port=$SDDCTX_DB_PORT \
  --user=root --execute="DROP DATABASE IF EXISTS sdd_context;"

# 清空规格变更演示使用的资料库
mysql --no-defaults --protocol=TCP --host=$SDDCTX_DB_HOST --port=$SDDCTX_DB_PORT \
  --user=root --execute="DROP DATABASE IF EXISTS sdd_context_spec_demo;"

# 还原示例工程的源码（若已应用代码改动）
git checkout -- minimax/sample_project/src/orderkit/repository.py
```

重置后重新执行第 5 节的步骤 1 即可从空状态复现整个样例。

## 8. 已验证结果

本次提交中实际执行记录见 `evidence/`：

| 证据 | 路径 |
| --- | --- |
| 端到端验证逐步日志 | `evidence/verification/<时间戳>/` |
| 数据库查询记录 | `evidence/database/<时间戳>/database-usage.json` |
| 规格变更前后对比 | `evidence/spec-update/<时间戳>/comparison.json` |
| PowerContext 服务重启后读回状态 | `evidence/powercontext/<时间戳>/restart-demo.json` |
| MiniMax 请求、响应与代码差异 | `evidence/verification/<时间戳>/minimax/` |

其中修改前的目标测试结果为 5 失败 1 通过（失败原因均为 `TypeError: ... unexpected
keyword argument 'status'`），回归测试 7 条全部通过——证明失败来自目标功能尚未
实现，而不是环境或依赖问题。

服务重启演示（`scripts/powercontext_restart_demo.py`）会读取任务状态、停止本地
PowerContext 服务、用同一数据目录重新启动，再次读取状态，结果记录在
`restart-demo.json`：`state_found_after_restart` 为 `true`，`specs_preserved`、
`code_preserved`、`target_tests_preserved` 均为 `true`。
