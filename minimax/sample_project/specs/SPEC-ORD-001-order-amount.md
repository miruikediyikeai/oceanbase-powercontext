# SPEC-ORD-001 订单金额计算

- id: SPEC-ORD-001
- title: 订单金额计算
- status: active
- tags: pricing, amount

## SPEC-ORD-001-R1 金额定义

订单金额等于单价乘以数量。单价以「分」为单位的整数表示，数量为正整数，
计算结果同样以「分」表示，不做四舍五入。

## SPEC-ORD-001-R2 非法数量

订单数量必须为正整数。数量为零或负数时抛出 `ValueError`，错误消息需包含
`quantity`。
