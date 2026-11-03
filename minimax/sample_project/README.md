# orderkit — 订单结算示例业务工程

这是「规有所依」SDD 上下文模块所使用的示例业务工程。它是一个可以独立运行、
带单元测试的小型订单结算库，用于演示「规格 → 代码 → 测试」三者在开发任务中的
关联关系。

## 目录结构

```
specs/                     SDD 规格（Markdown，含稳定的规则编号）
  SPEC-ORD-001-order-amount.md
  SPEC-ORD-002-coupon.md
  SPEC-ORD-003-order-status-filter.md
src/orderkit/              业务代码
  __init__.py
  models.py                订单与订单状态模型
  pricing.py               金额与优惠券计算
  repository.py            订单仓储与查询
tests/                     测试
  test_models.py           回归测试
  test_pricing.py          回归测试
  test_repository_filter.py 目标功能测试（本次任务新增）
```

## 运行测试

```bash
python -m pytest tests -q
```

## 本次开发任务（T-001）

实现 `SPEC-ORD-003`：为 `OrderRepository.list_orders` 增加按订单状态筛选的能力，
并处理非法状态与空结果两个边界。详见 `specs/SPEC-ORD-003-order-status-filter.md`。
