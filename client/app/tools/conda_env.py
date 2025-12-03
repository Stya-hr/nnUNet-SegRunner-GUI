import os
import subprocess
from typing import List, Tuple, Optional


def list_conda_envs() -> List[Tuple[str, str]]:
    """返回 (name, prefix) 列表；若无法解析 name，则使用空字符串，但保留 prefix。"""
    try:
        proc = subprocess.run(["conda", "env", "list"], capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            return []
        envs: List[Tuple[str, str]] = []
        for ln in proc.stdout.splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            parts = ln.split()
            if len(parts) >= 2:
                name = parts[0]
                if parts[1] == "*" and len(parts) >= 3:
                    prefix = parts[2]
                else:
                    prefix = parts[1]
                if os.path.isabs(prefix):
                    envs.append((name, prefix))
            elif len(parts) == 1 and os.path.isabs(parts[0]):
                envs.append(("", parts[0]))
        return envs
    except Exception:
        return []


def resolve_nnunet_exe(prefix: Optional[str]) -> Optional[str]:
    """根据 conda 前缀解析 nnUNetv2_predict 可执行文件路径。"""
    if not prefix:
        return None
    candidates = [
        os.path.join(prefix, "Scripts", "nnUNetv2_predict.exe"),
        os.path.join(prefix, "bin", "nnUNetv2_predict"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.normpath(c)
    return None

