# SPEC-ORD-002 优惠券折扣

- id: SPEC-ORD-002
- title: 优惠券折扣
- status: active
- tags: pricing, coupon

## SPEC-ORD-002-R1 满减券

优惠券包含门槛金额与抵扣金额两个整数，均以「分」表示。订单金额达到门槛时
抵扣固定金额，抵扣后金额不得小于零。

## SPEC-ORD-002-R2 未达门槛

订单金额未达到门槛金额时不使用该优惠券，返回原始金额，不抛异常。

## SPEC-ORD-002-R3 空优惠券

未提供优惠券时返回原始金额。
