"""订单与订单状态模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OrderStatus(str, Enum):
    """订单状态。取值同时是字符串，便于持久化与筛选。"""

    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Order:
    """一条订单记录。金额单位为「分」。"""

    order_id: str
    customer_id: str
    status: OrderStatus
    unit_price: int
    quantity: int

    @property
    def amount(self) -> int:
        """订单金额，等于单价乘以数量。"""
        if self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        return self.unit_price * self.quantity
