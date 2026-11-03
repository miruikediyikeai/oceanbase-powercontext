"""允许通过 `python -m sddctx` 调用命令行。"""

from __future__ import annotations

from sddctx.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
