"""回归测试：金额与优惠券计算。"""

from __future__ import annotations

from orderkit.pricing import Coupon, apply_coupon


def test_coupon_is_applied_when_threshold_is_met() -> None:
    coupon = Coupon("SAVE10", 1000, 300)
    assert apply_coupon(1500, coupon) == 1200


def test_coupon_is_ignored_below_threshold() -> None:
    coupon = Coupon("SAVE10", 1000, 300)
    assert apply_coupon(900, coupon) == 900


def test_discount_never_goes_below_zero() -> None:
    coupon = Coupon("SAVE10", 100, 500)
    assert apply_coupon(200, coupon) == 0


def test_no_coupon_keeps_amount() -> None:
    assert apply_coupon(2000, None) == 2000
