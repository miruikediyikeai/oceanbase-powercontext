# SPEC-ORD-003 订单状态筛选

- id: SPEC-ORD-003
- title: 订单状态筛选
- status: active
- tags: repository, filter, status

## SPEC-ORD-003-R1 筛选语义

`OrderRepository.list_orders` 接受一个可选参数 `status`，用于按订单状态筛选。
不传该参数时返回全部订单，保持既有行为不变；传入该参数时只返回状态匹配的订单。
参数为 `None` 等价于不筛选。

## SPEC-ORD-003-R2 状态取值

`status` 参数接受 `orderkit.models.OrderStatus` 枚举成员，或与枚举取值相同的字符串。
字符串比较忽略大小写，例如 `"PAID"` 与 `"paid"` 等价。

## SPEC-ORD-003-R3 非法状态

`status` 参数不是合法状态时抛出 `ValueError`，错误消息需包含 `status`
以及全部合法状态的取值，便于调用方定位问题。

## SPEC-ORD-003-R4 空结果

筛选后没有匹配的订单时返回空列表，不返回 `None`，不抛出异常。
