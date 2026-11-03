"""订单仓储与查询。"""

from __future__ import annotations

from collections.abc import Iterable

from orderkit.models import Order


class OrderRepository:
    """内存订单仓储。"""

    def __init__(self, orders: Iterable[Order] = ()) -> None:
        self._orders = list(orders)

    def list_orders(self) -> list[Order]:
        """返回全部订单。"""
        return list(self._orders)

    def find_by_id(self, order_id: str) -> Order | None:
        """按订单号查找订单，找不到时返回 None。"""
        for order in self._orders:
            if order.order_id == order_id:
                return order
        return None
