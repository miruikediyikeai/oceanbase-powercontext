"""回归测试：订单模型。"""

from __future__ import annotations

import pytest
from orderkit.models import Order, OrderStatus


def test_amount_is_unit_price_times_quantity() -> None:
    order = Order("o-1", "c-1", OrderStatus.PAID, 1250, 3)
    assert order.amount == 3750


def test_non_positive_quantity_is_rejected() -> None:
    order = Order("o-1", "c-1", OrderStatus.PAID, 1250, 0)
    with pytest.raises(ValueError, match="quantity"):
        _ = order.amount


def test_status_values_are_stable() -> None:
    assert [status.value for status in OrderStatus] == ["pending", "paid", "shipped", "cancelled"]
