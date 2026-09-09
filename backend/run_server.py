"""PyInstaller 入口：把 FastAPI 后端冻结成独立 exe，作为桌面版(Tauri)的 sidecar。

端口/主机通过环境变量传入；默认 127.0.0.1:8010。数据(.env + data/agent.db)从
**工作目录**读取——桌面版以 backend 目录（或 %APPDATA% 数据目录）为 cwd 启动本进程，
所以无需 Python 环境即可跑起完整后端。
"""
import os


def main() -> None:
    host = os.environ.get("MATRIX_HOST", "127.0.0.1")
    port = int(os.environ.get("MATRIX_PORT", "8010"))
    import uvicorn
    from app.main import app

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
