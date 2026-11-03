"""金额与优惠券计算。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Coupon:
    """满减券。门槛金额与抵扣金额单位均为「分」。"""

    code: str
    threshold: int
    discount: int


def apply_coupon(amount: int, coupon: Coupon | None) -> int:
    """按优惠券计算实付金额。"""
    if coupon is None:
        return amount
    if amount < coupon.threshold:
        return amount
    return max(amount - coupon.discount, 0)
