"""Start both companion services from .env; keep the console open, Ctrl+C to stop."""
import os
from pathlib import Path
import subprocess
import sys
import time

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def main():
    load_dotenv(ROOT / ".env")
    if not os.getenv("CABINET_TOKEN"):
        raise SystemExit("请复制 .env.example 为 .env，填写随机 CABINET_TOKEN。")
    host = os.getenv("CABINET_BIND_HOST", "127.0.0.1")
    processes = []
    try:
        memory = subprocess.Popen([sys.executable, "serve_memory.py"], cwd=ROOT)
        processes.append(memory)
        adapter = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", host,
                                    "--port", "8812", "--workers", "1"], cwd=ROOT)
        processes.append(adapter)
        print("体验入口：http://127.0.0.1:8812（需先配置并发布 Dify 应用）", flush=True)
        while all(p.poll() is None for p in processes):
            time.sleep(1)
        raise SystemExit("有服务退出，请检查上方日志。")
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(5)


if __name__ == "__main__":
    main()
