"""规有所依：面向软件持续演进的 SDD 上下文模块。

模块按开发任务检索适用规格、待改代码与验证测试，并把任务进展写入 PowerContext
以便跨进程复用。工程资料保存在 seekdb / OceanBase 中，检索结果直接进入上下文。
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
