import os
import sys
import uvicorn
import socket

# 用法：
# - 读取环境变量 PORT 或 NNUNET_PORT 设置端口，默认 8000
# - 可通过命令行参数指定端口：python run_server.py 9000
# - Host 默认 0.0.0.0，可用 NNUNET_HOST 或 HOST 环境变量覆盖

def main():
    # 确保可导入同目录下的 remote_api
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    # 解析端口
    port = None
    # 优先命令行参数
    if len(sys.argv) >= 2:
        try:
            port = int(sys.argv[1])
        except Exception:
            port = None
    # 其次环境变量
    if port is None:
        for name in ("PORT", "NNUNET_PORT"):
            val = os.environ.get(name)
            if val:
                try:
                    port = int(val)
                    break
                except Exception:
                    pass
    if port is None:
        port = 8000

    # Host
    host = os.environ.get("NNUNET_HOST") or os.environ.get("HOST") or "0.0.0.0"

    # 打印可用访问 URL 提示
    try:
        urls = []
        urls.append(f"http://127.0.0.1:{port}/")
        try:
            hn = socket.gethostname()
            ip_guess = socket.gethostbyname(hn)
            if ip_guess and ip_guess != "127.0.0.1":
                urls.append(f"http://{ip_guess}:{port}/")
        except Exception:
            pass
        if host and host != "0.0.0.0":
            urls.append(f"http://{host}:{port}/")
        print("nnUNet Remote Service 启动中，可用访问地址：")
        for u in urls:
            print("  ", u)
    except Exception:
        pass

    # 直接导入 app，避免模块名解析失败
    try:
        from remote_api import app
        uvicorn.run(app, host=host, port=port)
    except Exception:
        # 兜底：仍尝试字符串模块路径（若以项目根运行）
        uvicorn.run("remote_api:app", host=host, port=port)


if __name__ == "__main__":
    main()
