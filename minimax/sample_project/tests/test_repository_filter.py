"""目标功能测试：SPEC-ORD-003 订单状态筛选。

这些断言描述 SPEC-ORD-003 的期望行为，实现前必须失败、实现后必须全部通过。
"""

from __future__ import annotations

import pytest
from orderkit.models import Order, OrderStatus
from orderkit.repository import OrderRepository


@pytest.fixture
def repository() -> OrderRepository:
    return OrderRepository(
        [
            Order("o-1", "c-1", OrderStatus.PENDING, 1000, 1),
            Order("o-2", "c-2", OrderStatus.PAID, 2000, 2),
            Order("o-3", "c-3", OrderStatus.SHIPPED, 3000, 1),
        ]
    )


def test_list_orders_without_filter_returns_all(repository: OrderRepository) -> None:
    assert [order.order_id for order in repository.list_orders()] == ["o-1", "o-2", "o-3"]


def test_list_orders_filters_by_status(repository: OrderRepository) -> None:
    orders = repository.list_orders(status="paid")
    assert [order.order_id for order in orders] == ["o-2"]


def test_list_orders_accepts_status_enum(repository: OrderRepository) -> None:
    orders = repository.list_orders(status=OrderStatus.SHIPPED)
    assert [order.order_id for order in orders] == ["o-3"]


def test_status_filter_is_case_insensitive(repository: OrderRepository) -> None:
    orders = repository.list_orders(status="PAID")
    assert [order.order_id for order in orders] == ["o-2"]


def test_unknown_status_raises_value_error(repository: OrderRepository) -> None:
    with pytest.raises(ValueError) as excinfo:
        repository.list_orders(status="unknown")
    message = str(excinfo.value)
    assert "status" in message
    for status in OrderStatus:
        assert status.value in message


def test_filter_without_match_returns_empty_list() -> None:
    only_pending = OrderRepository([Order("o-9", "c-9", OrderStatus.PENDING, 1000, 1)])
    assert only_pending.list_orders(status="cancelled") == []
