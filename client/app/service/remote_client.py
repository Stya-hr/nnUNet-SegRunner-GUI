import os
import time
import uuid
from typing import Optional, Tuple, Dict, Any, Callable

import requests


class RemoteNnUNetClient:
    def __init__(self, base_url: str, timeout: float = 10.0, use_test_endpoints: bool | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.timeout = timeout
        # 当环境变量 USE_REMOTE_TEST_ENDPOINTS=1 时，切换到 /test/* 端点
        if use_test_endpoints is None:
            use_test_endpoints = os.environ.get("USE_REMOTE_TEST_ENDPOINTS", "0") == "1"
        self.use_test_endpoints = bool(use_test_endpoints)

    def start_job(
        self,
        in_dir: str,
        out_dir: str,
        dataset: str,
        config: str,
        folds: str,
    ) -> str:
        url = f"{self.base_url}/{'test/jobs' if self.use_test_endpoints else 'jobs'}"
        payload = {
            "in_dir": in_dir,
            "out_dir": out_dir,
            "dataset": dataset,
            "config": config,
            "folds": folds,
        }
        r = self.session.post(url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        return data.get("job_id")

    def get_progress(self, job_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/jobs/{job_id}/progress"
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def upload_and_start(
        self,
        file_path: str,
        dataset: str,
        config: str = "3d_fullres",
        folds: str = "0",
        image_id: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Dict[str, Any]:
        # 发送ZIP或NIfTI到远端并启动作业
        url = f"{self.base_url}/{'test/upload' if self.use_test_endpoints else 'upload'}"
        files = {"file": open(file_path, "rb")}
        data = {"dataset": dataset, "config": config, "folds": folds}
        if image_id:
            data["image_id"] = image_id
        if date:
            data["date"] = date
        try:
            r = self.session.post(url, files=files, data=data, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        finally:
            try:
                files["file"].close()
            except Exception:
                pass

    def download_result_zip(self, job_id: str, save_path: str) -> str:
        url = f"{self.base_url}/result/{job_id}"
        r = self.session.get(url, timeout=self.timeout, stream=True)
        r.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return save_path

    def wait_until_done(
        self,
        job_id: str,
        on_progress: Optional[Callable[[int, str], None]] = None,
        poll_interval: float = 0.5,
    ) -> Tuple[str, Optional[str]]:
        last_pct = -1
        last_line = None
        while True:
            data = self.get_progress(job_id)
            status = data.get("status")
            pct = int(data.get("percent") or 0)
            line = data.get("line") or ""
            if on_progress and (pct != last_pct or line != last_line):
                try:
                    on_progress(pct, line)
                except Exception:
                    pass
                last_pct, last_line = pct, line
            if status in ("success", "failed"):
                return status, data.get("error")
            time.sleep(poll_interval)
