import os
import re
import sys
import shutil
import subprocess
import threading
import time
import uuid
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from contextlib import asynccontextmanager
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="nnUNet Remote API", version="0.1.0")


class JobRequest(BaseModel):
    in_dir: str
    out_dir: str
    dataset: str
    config: str
    folds: str


class JobState:
    def __init__(self):
        self.status: str = "running"  # running|success|failed
        self.percent: int = 0
        self.line: str = ""
        self.error: Optional[str] = None
        self.thread: Optional[threading.Thread] = None
        self.proc: Optional[subprocess.Popen] = None
        # metadata
        self.image_id: str | None = None
        self.date: str | None = None
        self.client_ip: str | None = None
        # per-job 模拟标记：若为 True，则不调用 nnUNet，使用模拟器
        self.use_sim: bool = False


_jobs: Dict[str, JobState] = {}
_job_out_dirs: Dict[str, str] = {}
_job_tmp_roots: Dict[str, str] = {}
_job_downloaded: Dict[str, bool] = {}
_jobs_lock = threading.Lock()

_NNUNET_EXE: Optional[str] = None
_CONDA_PREFIX_SELECTED: Optional[str] = None

def _resolve_nnunet_exe(interactive: bool = False) -> Optional[str]:
    """
    通过用户在启动时交互选择 Conda 环境前缀来解析 nnUNetv2_predict。
    - 首次解析：若 interactive=True，在控制台提示输入 Conda 环境路径；否则使用 NNUNET_CONDA_PREFIX/CONDA_PREFIX。
    - 后续解析：使用已缓存的前缀拼接可执行路径。
    Windows 寻址 Scripts/nnUNetv2_predict.exe；Linux/Unix 寻址 bin/nnUNetv2_predict。
    """
    global _NNUNET_EXE, _CONDA_PREFIX_SELECTED
    if _NNUNET_EXE and os.path.isfile(_NNUNET_EXE):
        return _NNUNET_EXE

    if not _CONDA_PREFIX_SELECTED:
        default_prefix = os.environ.get("NNUNET_CONDA_PREFIX") or os.environ.get("CONDA_PREFIX")
        if interactive:
            try:
                envs = _list_conda_envs()
                if envs:
                    print("检测到以下 Conda 环境（索引：名称 -> 前缀）：")
                    for idx, (name, prefix) in enumerate(envs):
                        print(f"  [{idx}] {name} -> {prefix}")
                    print("请输入要使用的环境索引，或直接输入路径；回车使用默认前缀。")
                prompt = f"选择 (index/路径) [{default_prefix or ''}]: "
                val = input(prompt).strip()
                chosen: Optional[str] = None
                if val == "":
                    chosen = default_prefix
                elif val.isdigit() and envs:
                    i = int(val)
                    if 0 <= i < len(envs):
                        chosen = envs[i][1]
                else:
                    chosen = val
                _CONDA_PREFIX_SELECTED = chosen if chosen else None
            except Exception:
                _CONDA_PREFIX_SELECTED = default_prefix
        else:
            _CONDA_PREFIX_SELECTED = default_prefix

    if not _CONDA_PREFIX_SELECTED:
        return None

    candidates = [
        os.path.join(_CONDA_PREFIX_SELECTED, "Scripts", "nnUNetv2_predict.exe"),
        os.path.join(_CONDA_PREFIX_SELECTED, "bin", "nnUNetv2_predict"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            _NNUNET_EXE = os.path.normpath(c)
            return _NNUNET_EXE
    return None

def _list_conda_envs() -> list[tuple[str, str]]:
    """调用 `conda env list` 并解析出 (name, prefix) 列表。失败返回空列表。"""
    try:
        # 在 Windows cmd 环境下直接调用 conda；用户需确保 conda 在 PATH 或使用已激活环境
        proc = subprocess.run(["conda", "env", "list"], capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            return []
        lines = proc.stdout.splitlines()
        envs: list[tuple[str, str]] = []
        for ln in lines:
            ln = ln.strip()
            # 典型行：base *  C:\Miniconda3
            #        your_env    C:\Miniconda3\envs\your_env
            if not ln or ln.startswith("#"):
                continue
            parts = ln.split()
            if len(parts) >= 2:
                name = parts[0]
                # 去掉可能的星号标记
                if parts[1] == "*" and len(parts) >= 3:
                    prefix = parts[2]
                else:
                    prefix = parts[1]
                # 过滤非路径内容
                if os.path.isabs(prefix):
                    envs.append((name, prefix))
        return envs
    except Exception:
        return []


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 服务启动：提示选择 Conda 环境并解析 nnUNet
    try:
        # 当使用模拟器时，跳过 nnUNet 解析，以便进行纯文件传输与流程测试
        if os.environ.get("USE_NNUNET_SIM", "0") == "1":
            print("[lifespan:start] 模拟器已启用，跳过 nnUNetv2_predict 解析。")
        else:
            exe = _resolve_nnunet_exe(interactive=True)
            if exe:
                print(f"[lifespan:start] 已选择 Conda 前缀: {_CONDA_PREFIX_SELECTED}")
                print(f"[lifespan:start] nnUNetv2_predict 路径: {exe}")
            else:
                print("[lifespan:start] 未能解析 nnUNetv2_predict，请在控制台输入 Conda 环境路径或设置 NNUNET_CONDA_PREFIX/CONDA_PREFIX。")
    except Exception as e:
        print(f"[lifespan:start] 解析 nnUNet 失败: {e}")
    yield
    # 服务关闭：目前无需特殊清理

app.router.lifespan_context = lifespan


@app.post("/jobs")
def create_job(req: JobRequest, simulate: bool = False):
    job_id = str(uuid.uuid4())
    st = JobState()
    st.use_sim = bool(simulate)
    with _jobs_lock:
        _jobs[job_id] = st
        _job_out_dirs[job_id] = req.out_dir

    def run_job():
        try:
            os.makedirs(req.out_dir, exist_ok=True)
            if st.use_sim or os.environ.get("USE_NNUNET_SIM", "0") == "1":
                _run_simulator(st)
                return
            cmd = _build_predict_command(req.in_dir, req.out_dir, req.dataset, req.config, req.folds)
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )
            st.proc = proc
            for line in proc.stdout or []:
                pct = _parse_percent(line)
                if pct is not None:
                    st.percent = max(0, min(100, int(pct)))
                st.line = line.strip()[-200:]
            ret = proc.wait()
            if ret != 0:
                st.status = "failed"
                st.error = f"process exit {ret}"
                return
            st.status = "success"
            st.percent = max(st.percent, 100)
        except Exception as e:
            st.status = "failed"
            st.error = str(e)

    th = threading.Thread(target=run_job, daemon=True)
    st.thread = th
    th.start()
    return {"job_id": job_id}


@app.get("/health")
def health():
    # 简单健康检查：返回服务可用与 nnUNet 命令探测结果
    exe = _resolve_nnunet_exe(interactive=False)
    nnunet = bool(exe)
    return {"ok": True, "nnunet": nnunet, "exe_path": exe or None, "conda_prefix": _CONDA_PREFIX_SELECTED or None}


@app.get("/jobs/{job_id}/progress")
def get_progress(job_id: str):
    st = _jobs.get(job_id)
    if not st:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "status": st.status,
        "percent": st.percent,
        "line": st.line,
        "error": st.error,
        "meta": {
            "image_id": st.image_id,
            "date": st.date,
            "client_ip": st.client_ip,
        },
    }


def _build_predict_command(in_dir: str, out_dir: str, tag_id: str, config: str, folds: str):
    exe = _resolve_nnunet_exe(interactive=True)
    if not exe:
        raise FileNotFoundError(
            "nnUNetv2_predict 未找到。请在启动控制台输入 Conda 环境路径，或设置 NNUNET_CONDA_PREFIX/CONDA_PREFIX。"
        )
    return [exe, "-i", in_dir, "-o", out_dir, "-d", tag_id, "-c", config, "-f", folds]


def _parse_percent(line: str) -> Optional[int]:
    m = re.search(r"(\d{1,3})%", line)
    if m:
        return int(m.group(1))
    m2 = re.search(r"\b(\d+)\s*/\s*(\d+)\b", line)
    if m2:
        a, b = int(m2.group(1)), int(m2.group(2))
        if b > 0 and b <= 10000 and a <= b:
            return int(a * 100 / b)
    return None


def _run_simulator(st: JobState):
    # 纯本地模拟：耗时递增并生成一个结果文件以模拟成功
    try:
        for i in range(0, 101, 2):
            st.percent = i
            st.line = f"Simulating... {i}%"
            time.sleep(0.05)
        st.status = "success"
    except Exception as e:
        st.status = "failed"
        st.error = str(e)

# ------------------ 上传与结果下载（可选） ------------------
@app.post("/upload")
def upload_case(
    request: Request,
    file: UploadFile = File(...),
    dataset: str = Form(...),
    config: str = Form("3d_fullres"),
    folds: str = Form("0"),
    image_id: str = Form("") ,
    date: str = Form(""),
):
    # 接收一个ZIP或单NIfTI，解压/保存到临时输入目录，启动作业并返回 job_id
    import tempfile, zipfile
    tmp_root = tempfile.mkdtemp(prefix="nnunet_upload_")
    in_dir = os.path.join(tmp_root, "in")
    out_dir = os.path.join(tmp_root, "out")
    os.makedirs(in_dir, exist_ok=True); os.makedirs(out_dir, exist_ok=True)
    fn = file.filename or "upload.bin"
    fp = os.path.join(tmp_root, fn)
    with open(fp, "wb") as f:
        shutil.copyfileobj(file.file, f)
    # 如果是ZIP则解压，否则直接复制到输入目录
    lower = fn.lower()
    try:
        if lower.endswith(".zip"):
            with zipfile.ZipFile(fp, "r") as zf:
                zf.extractall(in_dir)
        else:
            # 支持 .nii/.nii.gz
            ext = ".nii.gz" if lower.endswith(".nii.gz") else ".nii"
            dst = os.path.join(in_dir, f"case_0000{ext}")
            shutil.copy2(fp, dst)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid upload: {e}")

    # 创建作业
    req = JobRequest(in_dir=in_dir, out_dir=out_dir, dataset=dataset, config=config, folds=folds)
    job = create_job(req)
    jid = job["job_id"]
    # 记录元数据
    with _jobs_lock:
        st = _jobs.get(jid)
        if st:
            st.image_id = (image_id or None)
            st.date = (date or None)
            try:
                st.client_ip = request.client.host if request and request.client else None
            except Exception:
                st.client_ip = None
        # 记录临时根目录，用于后续清理
        _job_tmp_roots[jid] = tmp_root

    # 启动后台清理：作业完成后立即删除临时根目录（含上传影像与输出）
    def _cleanup_when_done(job_id: str, root: str):
        try:
            # 轮询作业状态，完成或失败均清理
            while True:
                with _jobs_lock:
                    st2 = _jobs.get(job_id)
                    status = st2.status if st2 else None
                if status in ("success", "failed"):
                    break
                time.sleep(1.0)
            # 若成功，等待结果被客户端下载或超时（最多10分钟）
            if status == "success":
                deadline = time.time() + 600.0
                while time.time() < deadline:
                    with _jobs_lock:
                        downloaded = _job_downloaded.get(job_id, False)
                    if downloaded:
                        break
                    time.sleep(1.0)
            import shutil as _shutil
            _shutil.rmtree(root, ignore_errors=True)
            with _jobs_lock:
                _job_tmp_roots.pop(job_id, None)
                _job_out_dirs.pop(job_id, None)
                _job_downloaded.pop(job_id, None)
        except Exception:
            pass

    try:
        th = threading.Thread(target=_cleanup_when_done, args=(jid, tmp_root), daemon=True)
        th.start()
    except Exception:
        pass
    return {"job_id": jid, "in_dir": in_dir, "out_dir": out_dir}

# ------------------ 专用测试端点（强制模拟处理） ------------------
@app.post("/test/jobs")
def create_test_job(req: JobRequest):
    # 与 /jobs 相同，但强制使用模拟器
    return create_job(req, simulate=True)

@app.post("/test/upload")
def upload_case_test(
    request: Request,
    file: UploadFile = File(...),
    dataset: str = Form(...),
    config: str = Form("3d_fullres"),
    folds: str = Form("0"),
    image_id: str = Form(""),
    date: str = Form(""),
):
    # 与 /upload 相同的流程，但创建作业时强制模拟
    import tempfile, zipfile
    tmp_root = tempfile.mkdtemp(prefix="nnunet_upload_")
    in_dir = os.path.join(tmp_root, "in")
    out_dir = os.path.join(tmp_root, "out")
    os.makedirs(in_dir, exist_ok=True); os.makedirs(out_dir, exist_ok=True)
    fn = file.filename or "upload.bin"
    fp = os.path.join(tmp_root, fn)
    with open(fp, "wb") as f:
        shutil.copyfileobj(file.file, f)
    lower = fn.lower()
    try:
        if lower.endswith(".zip"):
            with zipfile.ZipFile(fp, "r") as zf:
                zf.extractall(in_dir)
        else:
            ext = ".nii.gz" if lower.endswith(".nii.gz") else ".nii"
            dst = os.path.join(in_dir, f"case_0000{ext}")
            shutil.copy2(fp, dst)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid upload: {e}")

    req = JobRequest(in_dir=in_dir, out_dir=out_dir, dataset=dataset, config=config, folds=folds)
    job = create_job(req, simulate=True)
    jid = job["job_id"]
    with _jobs_lock:
        st = _jobs.get(jid)
        if st:
            st.image_id = (image_id or None)
            st.date = (date or None)
            try:
                st.client_ip = request.client.host if request and request.client else None
            except Exception:
                st.client_ip = None
        _job_tmp_roots[jid] = tmp_root

    def _cleanup_when_done(job_id: str, root: str):
        try:
            while True:
                with _jobs_lock:
                    st2 = _jobs.get(job_id)
                    status = st2.status if st2 else None
                if status in ("success", "failed"):
                    break
                time.sleep(1.0)
            if status == "success":
                deadline = time.time() + 600.0
                while time.time() < deadline:
                    with _jobs_lock:
                        downloaded = _job_downloaded.get(job_id, False)
                    if downloaded:
                        break
                    time.sleep(1.0)
            import shutil as _shutil
            _shutil.rmtree(root, ignore_errors=True)
            with _jobs_lock:
                _job_tmp_roots.pop(job_id, None)
                _job_out_dirs.pop(job_id, None)
                _job_downloaded.pop(job_id, None)
        except Exception:
            pass

    try:
        th = threading.Thread(target=_cleanup_when_done, args=(jid, tmp_root), daemon=True)
        th.start()
    except Exception:
        pass
    return {"job_id": jid, "in_dir": in_dir, "out_dir": out_dir}

@app.get("/result/{job_id}")
def download_result(job_id: str):
    # 将输出目录打包为ZIP并返回文件
    st = _jobs.get(job_id)
    if not st:
        raise HTTPException(status_code=404, detail="job not found")
    if st.status != "success":
        raise HTTPException(status_code=409, detail=f"job not finished: {st.status}")
    out_dir = _job_out_dirs.get(job_id)
    if not out_dir or not os.path.isdir(out_dir):
        raise HTTPException(status_code=500, detail="missing out_dir")
    import tempfile, zipfile
    tmp_zip = os.path.join(tempfile.gettempdir(), f"nnunet_result_{job_id}.zip")
    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(out_dir):
                for f in files:
                    fp = os.path.join(root, f)
                    arc = os.path.relpath(fp, out_dir)
                    zf.write(fp, arc)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"zip failed: {e}")
    from fastapi.responses import FileResponse
    # 标记为已下载，以便后台清理线程尽快清理
    with _jobs_lock:
        _job_downloaded[job_id] = True
    return FileResponse(tmp_zip, filename="result.zip")


# ------------------ 简易监控 UI ------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
        # 简单 HTML：显示健康状态、当前 nnUNet 路径、作业列表与最新日志（只监控与查看，不提供上传）
        exe_path = _resolve_nnunet_exe(interactive=False)
        nnunet_ok = bool(exe_path)
        rows = []
        with _jobs_lock:
                for jid, st in _jobs.items():
                        rows.append(
                                f"<tr><td>{jid}</td><td>{st.status}</td><td>{st.percent}%</td><td><code>{(st.line or '').replace('<','&lt;')}</code></td>"
                                f"<td>{'可下载' if st.status=='success' else ''}</td>"
                                f"<td>{f'<a href=\"/result/{jid}\">ZIP</a>' if st.status=='success' else ''}</td></tr>"
                        )
        rows_html = "\n".join(rows) or "<tr><td colspan=6>暂无作业</td></tr>"
        html = f"""
        <!doctype html>
        <html lang=zh-cn>
        <head>
            <meta charset=utf-8>
            <title>nnUNet Remote Service</title>
            <meta http-equiv="refresh" content="5"> <!-- 每5秒自动刷新 -->
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; font-size: 13px; }}
                th {{ background: #f5f5f5; text-align: left; }}
                .ok {{ color: #2ecc71; }} .bad {{ color: #e74c3c; }}
                .card {{ border: 1px solid #ddd; padding: 12px; margin-bottom: 16px; border-radius: 6px; }}
                input[type=text], input[type=file] {{ padding: 6px; font-size: 13px; }}
                button {{ padding: 6px 12px; font-size: 13px; }}
                code {{ white-space: pre-wrap; }}
                .mono {{ font-family: Consolas, monospace; font-size: 12px; }}
            </style>
        </head>
        <body>
            <h2>nnUNet 远程服务</h2>
            <div class=card>
                健康状态：<span class="{ 'ok' if nnunet_ok else 'bad' }">nnUNetv2_predict { '可用' if nnunet_ok else '未发现' }</span><br/>
                当前路径：<span class="mono">{(exe_path or '未找到')}</span>
                <br/>Conda 前缀：<span class="mono">{(_CONDA_PREFIX_SELECTED or '未选择')}</span>
            </div>

            <div class=card>
                <h3>作业列表（含最新日志）</h3>
                <table>
                    <thead><tr><th>Job ID</th><th>影像ID</th><th>日期</th><th>来源IP</th><th>状态</th><th>进度</th><th>最后输出</th><th>结果</th><th>下载</th></tr></thead>
                    <tbody>
                        {"".join([
                            f"<tr><td>{jid}</td><td>{(_jobs[jid].image_id or '')}</td><td>{(_jobs[jid].date or '')}</td><td>{(_jobs[jid].client_ip or '')}</td>"
                            f"<td>{_jobs[jid].status}</td><td>{_jobs[jid].percent}%</td><td><code>{(_jobs[jid].line or '').replace('<','&lt;')}</code></td>"
                            f"<td>{'可下载' if _jobs[jid].status=='success' else ''}</td>"
                            f"<td>{f'<a href=\"/result/{jid}\">ZIP</a>' if _jobs[jid].status=='success' else ''}</td></tr>"
                            for jid in _jobs.keys()
                        ]) or "<tr><td colspan=9>暂无作业</td></tr>"}
                    </tbody>
                </table>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html)

# 运行方式：
# uvicorn remote_api:app --host 0.0.0.0 --port 8000
