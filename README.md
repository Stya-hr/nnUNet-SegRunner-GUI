# nnUNet-SegRunner-GUI

简体中文 | English coming soon

一个用于影像分割后处理的桌面客户端与可选远程推理服务。客户端基于 PySide6，支持调用本地 `nnUNetv2_predict` 或通过 HTTP 调用远端服务执行推理。

---

## 功能特性
- 图形界面选择输入/输出目录，管理分割任务
- 一键调用 nnU‑Net v2 推理（本地或远程）
- 任务标签预设与参数配置（`-d/-c/-f`）
- 逐例处理模式，提升对特殊路径/多病例的稳健性
- 结果 ZIP 导出

## 快速开始（Windows）

在仓库根目录或项目目录运行以下命令（示例中使用占位路径，请替换为你的项目路径）：

```cmd
cd /d D:\path\to\project
python -m venv .venv
.venv\Scripts\activate
pip install -r client\app\requirements.txt
python -m client.main
```

或直接：

```cmd
python client\main.py
```

提示：若使用本地推理，请确保 `nnUNetv2_predict` 可在当前环境中调用，并正确配置 `NNUNET_RESULTS` 指向模型结果目录。

## 远程推理（可选）
在远端机器上启动服务（替换为你的远程服务目录）：

```cmd
cd /d D:\path\to\project\remote-service
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn remote_api:app --host 0.0.0.0 --port 8000
```

客户端启用远程模式示例（将 `<REMOTE_HOST_OR_IP>` 替换为实际可访问的主机或域名）：

```cmd
set NNUNET_REMOTE_API=http://<REMOTE_HOST_OR_IP>:8000
python -m client.main
```

建议使用远端可访问的共享路径（例如 `\\<file-server>\share\case1`）作为输入/输出目录，确保服务进程具备读写权限。

## 配置与环境变量
- `NNUNET_REMOTE_API`：远端服务地址（示例：`http://<REMOTE_HOST_OR_IP>:8000`）。
- `NNUNET_RESULTS`：nnU‑Net v2 模型结果目录（用于本地或远端环境）。

## 使用说明
1. 启动 GUI 客户端。
2. 选择输入病例目录；（可选）选择输出目录。
3. 设置任务标签参数或使用预设快速填充。
4. 运行分割并在完成后导出 ZIP。

## 常见问题（FAQ）
- 找不到 `nnUNetv2_predict`：在本地模式下，请确认 nnU‑Net v2 已安装并可直接在当前环境调用；或改用远程模式。
- 远程无法读写目录：检查共享路径与权限，确保服务进程对输入/输出目录有读写权限。
- 进度与日志：远端服务会解析 nnU‑Net 输出并返回状态，客户端周期性查询显示进度与日志。

## 开发与调试
- 客户端开发：在仓库根目录运行 `python -m client.main`，查看日志输出与界面行为。
- 远端服务：基于 FastAPI/uvicorn，适于在容器或服务器部署。
