import os
import re
import sys
import shutil
import subprocess
import datetime
import tempfile
import time
from typing import Callable, Optional, Dict, List, Tuple

from app.model.task import Task, TaskStatus
from app.model.tasktag import TaskTagSpec


class NnUNetService:
    def __init__(self, use_test_endpoints: bool = False):
        self.use_test_endpoints = bool(use_test_endpoints)

    def _is_remote(self) -> Optional[str]:
        base = os.environ.get("NNUNET_REMOTE_API", "").strip()
        return base or None
    def _clear_output_dir(self, out_dir: str) -> None:
        # 仅清理我们生成的结果文件与日志/冗余JSON
        try:
            if not os.path.isdir(out_dir):
                return
            removable = {"predict_progress.log", "dataset.json", "plans.json", "predict_from_raw_data_args.json"}
            for name in os.listdir(out_dir):
                lower = name.lower()
                fp = os.path.join(out_dir, name)
                if os.path.isfile(fp):
                    if lower.endswith('.nii') or lower.endswith('.nii.gz') or name in removable:
                        try:
                            os.remove(fp)
                        except Exception:
                            pass
        except Exception:
            # 清理失败不应阻断主流程
            pass
    def _build_predict_command(self, in_dir: str, out_dir: str, tag_id: str, config: str, folds: str) -> List[str]:
        use_sim = os.environ.get("USE_NNUNET_SIM", "0") == "1"
        if use_sim:
            return [
                sys.executable,
                "-m",
                "app.tools.mock_nnunetv2_predict",
                "-i",
                in_dir,
                "-o",
                out_dir,
                "-d",
                tag_id,
                "-c",
                config,
                "-f",
                folds,
            ]
        exe = shutil.which("nnUNetv2_predict")
        if exe is None:
            raise FileNotFoundError(
                "未找到 nnUNetv2_predict。请安装 nnU-Net v2"
            )
        return [
            exe,
            "-i",
            in_dir,
            "-o",
            out_dir,
            "-d",
            tag_id,
            "-c",
            config,
            "-f",
            folds,
        ]
    def run_io_split(
        self,
        task: Task,
        task_tag: str | TaskTagSpec = "101",
        on_progress: Optional[Callable[[int, str], None]] = None,
    ) -> Task:
        task.started_at = datetime.datetime.now()
        task.status = TaskStatus.RUNNING

        # 强制逐例处理：统一走逐例实现
        return self.run_io_split_per_case(
            task,
            task_tag=task_tag,
            on_progress=on_progress,
        )
        images_path = task.images_path  # 不再使用批处理路径
        segs_save_path = (
            task.desired_output_dir.strip()
            if task.desired_output_dir
            else os.path.join(os.path.dirname(images_path), "seg")
        )
        segs_save_path = os.path.normpath(segs_save_path)
        os.makedirs(segs_save_path, exist_ok=True)
        # 每次运行前清理旧结果
        self._clear_output_dir(segs_save_path)

        # 解析任务标签：支持字符串或TaskTagSpec
        tag_id = "101"
        config = "3d_fullres"
        folds = "0"
        if isinstance(task_tag, TaskTagSpec):
            tag_id = task_tag.id.strip()
            config = (task_tag.config or "3d_fullres").strip()
            folds = (task_tag.folds or "0").strip()
        elif isinstance(task_tag, str):
            tag_id = task_tag.strip() or "101"

        log_file_path = os.path.join(segs_save_path, "predict_progress.log")

        def _emit_progress(pct: int, line: str):
            if on_progress:
                try:
                    on_progress(max(0, min(100, int(pct))), line)
                except Exception:
                    pass

        # 运行并解析进度（支持远程/本地）
        try:
            remote_api = self._is_remote()
            if remote_api:
                # 远程模式
                from app.service.remote_client import RemoteNnUNetClient
                client = RemoteNnUNetClient(remote_api, use_test_endpoints=self.use_test_endpoints)
                # 远程模式建议 in/out 为远端可访问的绝对路径（本地填写 UNC 共享）
                job_id = client.start_job(images_path, segs_save_path, tag_id, config, folds)
                status, err = client.wait_until_done(job_id, on_progress=_emit_progress)
                if status != "success":
                    raise RuntimeError(err or "远程任务失败")
            else:
                # 本地子进程
                command = self._build_predict_command(images_path, segs_save_path, tag_id, config, folds)
                with open(log_file_path, "a", encoding="utf-8", errors="ignore") as logf:
                    proc = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        universal_newlines=True,
                    )

                    last_pct = -1
                    last_line = None
                    last_emit_time = 0.0
                    throttle_ms = 200  # 节流阈值，毫秒
                    if proc.stdout is not None:
                        for line in proc.stdout:
                            try:
                                logf.write(line)
                            except Exception:
                                pass

                            # 解析百分比，如 "... 42% ..."
                            m = re.search(r"(\d{1,3})%", line)
                            pct = None
                            if m:
                                pct = int(m.group(1))
                            else:
                                # 尝试解析分数形式 x/y
                                m2 = re.search(r"\b(\d+)\s*/\s*(\d+)\b", line)
                                if m2:
                                    num = int(m2.group(1))
                                    den = int(m2.group(2))
                                    if den > 0 and den <= 10000 and num <= den:
                                        pct = int(num * 100 / den)

                            now = time.time() * 1000.0
                            should_emit = False
                            if pct is not None and pct != last_pct:
                                last_pct = pct
                                should_emit = True
                            else:
                                # 同一百分比下，仅当行内容变化或超过节流阈值才刷新
                                if line != last_line or (now - last_emit_time) >= throttle_ms:
                                    should_emit = True
                            if should_emit:
                                _emit_progress(last_pct if last_pct >= 0 else 0, line)
                                last_emit_time = now
                                last_line = line

                    ret = proc.wait()
                    if ret != 0:
                        raise subprocess.CalledProcessError(ret, command)
            # 清理nnUNet产生的冗余文件（若存在）
            for fname in [
                "dataset.json",
                "plans.json",
                "predict_from_raw_data_args.json",
            ]:
                fpath = os.path.join(segs_save_path, fname)
                if os.path.isfile(fpath):
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass

            task.output_path = segs_save_path
            task.status = TaskStatus.SUCCESS
        except Exception as e:
            task.error_message = str(e)
            task.status = TaskStatus.FAILED
        finally:
            task.finished_at = datetime.datetime.now()

        return task

    # 逐例处理：每个病例放入一个临时输入目录并运行一次nnUNet，以便计数与规避特殊字符
    def run_io_split_per_case(
        self,
        task: Task,
        task_tag: str | TaskTagSpec = "101",
        on_progress: Optional[Callable[[int, str], None]] = None,
        on_case_done: Optional[Callable[[str, str], None]] = None,
    ) -> Task:
        task.started_at = datetime.datetime.now()
        task.status = TaskStatus.RUNNING

        images_path = task.images_path
        segs_save_path = (
            task.desired_output_dir.strip()
            if task.desired_output_dir
            else os.path.join(os.path.dirname(images_path), "seg")
        )
        segs_save_path = os.path.normpath(segs_save_path)
        os.makedirs(segs_save_path, exist_ok=True)
        # 每次运行前清理旧结果
        self._clear_output_dir(segs_save_path)

        # 收集病例
        cases = self._collect_cases(images_path)
        total = len(cases)
        if total == 0:
            task.error_message = "未在输入目录找到可处理的影像文件(.nii/.nii.gz)"
            task.status = TaskStatus.FAILED
            task.finished_at = datetime.datetime.now()
            return task

        # 解析任务标签参数
        tag_id = "101"
        config = "3d_fullres"
        folds = "0"
        if isinstance(task_tag, TaskTagSpec):
            tag_id = task_tag.id.strip()
            config = (task_tag.config or "3d_fullres").strip()
            folds = (task_tag.folds or "0").strip()
        elif isinstance(task_tag, str):
            tag_id = task_tag.strip() or "101"

        try:
            # 远程模式：逐例处理改为逐例上传 -> 远端执行 -> 可选下载结果
            remote_api = self._is_remote()
            if remote_api:
                from app.service.remote_client import RemoteNnUNetClient
                client = RemoteNnUNetClient(remote_api, use_test_endpoints=self.use_test_endpoints)
                # 每例打包上传
                for idx, (case_id, file_list) in enumerate(cases):
                    base_pct = int(idx * 100 / total)

                    def _inner_progress(pct: int, line: str):
                        overall = base_pct + int(pct / max(1, total))
                        if on_progress:
                            try:
                                on_progress(min(99, overall), line)
                            except Exception:
                                pass

                    # 打包该病例为临时ZIP
                    tmp_root = tempfile.mkdtemp(prefix="nnunet_case_zip_")
                    zip_fp = os.path.join(tmp_root, f"{case_id}.zip")
                    try:
                        import zipfile
                        with zipfile.ZipFile(zip_fp, "w", zipfile.ZIP_DEFLATED) as zf:
                            for src in file_list:
                                zf.write(src, os.path.basename(src))
                        meta = client.upload_and_start(zip_fp, dataset=tag_id, config=config, folds=folds)
                        job_id = meta.get("job_id")
                        if not job_id:
                            raise RuntimeError("上传后未返回 job_id")
                        status, err = client.wait_until_done(job_id, on_progress=_inner_progress)
                        if status != "success":
                            raise RuntimeError(err or "远程任务失败")
                        # 下载结果ZIP到本地，并解压到最终输出目录
                        save_zip = os.path.join(segs_save_path, f"{case_id}.zip")
                        client.download_result_zip(job_id, save_zip)
                        try:
                            with zipfile.ZipFile(save_zip, "r") as zf:
                                zf.extractall(segs_save_path)
                        except Exception:
                            pass
                        if on_case_done:
                            try:
                                # 结果文件名未知，回传ZIP路径或目录
                                on_case_done(case_id, save_zip)
                            except Exception:
                                pass
                    finally:
                        try:
                            import shutil as _shutil
                            _shutil.rmtree(tmp_root, ignore_errors=True)
                        except Exception:
                            pass

                task.output_path = segs_save_path
                task.status = TaskStatus.SUCCESS
                if on_progress:
                    try:
                        on_progress(100, "done")
                    except Exception:
                        pass
                task.finished_at = datetime.datetime.now()
                return task
            for idx, (case_id, file_list) in enumerate(cases):
                base_pct = int(idx * 100 / total)

                def _inner_progress(pct: int, line: str):
                    # 将单例进度映射到总体进度
                    overall = base_pct + int(pct / max(1, total))
                    if on_progress:
                        try:
                            on_progress(min(99, overall), line)
                        except Exception:
                            pass

                out_path = self._run_single_case(
                    case_id=case_id,
                    file_list=file_list,
                    final_output_dir=segs_save_path,
                    tag_id=tag_id,
                    config=config,
                    folds=folds,
                    on_progress=_inner_progress,
                )
                if on_case_done and out_path:
                    try:
                        on_case_done(case_id, out_path)
                    except Exception:
                        pass

            # 最终清理冗余文件（逐例输出目录中不会生成这些通用文件，但保持一致）
            for fname in [
                "dataset.json",
                "plans.json",
                "predict_from_raw_data_args.json",
            ]:
                fpath = os.path.join(segs_save_path, fname)
                if os.path.isfile(fpath):
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass

            task.output_path = segs_save_path
            task.status = TaskStatus.SUCCESS
            if on_progress:
                try:
                    on_progress(100, "done")
                except Exception:
                    pass
        except Exception as e:
            task.error_message = str(e)
            task.status = TaskStatus.FAILED
        finally:
            task.finished_at = datetime.datetime.now()

        return task

    def _collect_cases(self, images_path: str) -> List[Tuple[str, List[str]]]:
        # 支持 .nii 与 .nii.gz；将同一病例的多通道文件聚合
        pattern_gz = re.compile(
            r"^(?P<id>.+)_(?P<ch>\d{4})\.nii\.gz$", re.IGNORECASE)
        pattern_nii = re.compile(
            r"^(?P<id>.+)_(?P<ch>\d{4})\.nii$", re.IGNORECASE)

        files = []
        for name in os.listdir(images_path):
            lower = name.lower()
            if lower.endswith(".nii.gz") or lower.endswith(".nii"):
                files.append(name)

        buckets: Dict[str, List[str]] = {}
        for fname in files:
            m = pattern_gz.match(fname) or pattern_nii.match(fname)
            if m:
                case_id = m.group("id")
            else:
                # 无通道后缀，则用去扩展名的基名
                case_id = (
                    os.path.splitext(os.path.splitext(fname)[0])[0]
                    if fname.lower().endswith(".nii.gz")
                    else os.path.splitext(fname)[0]
                )
            buckets.setdefault(case_id, []).append(
                os.path.join(images_path, fname))

        # 排序保证通道顺序稳定
        results: List[Tuple[str, List[str]]] = []
        for cid, flist in buckets.items():
            flist_sorted = sorted(flist)
            results.append((cid, flist_sorted))
        results.sort(key=lambda x: x[0])
        return results

    def _run_single_case(
        self,
        case_id: str,
        file_list: List[str],
        final_output_dir: str,
        tag_id: str,
        config: str,
        folds: str,
        on_progress: Optional[Callable[[int, str], None]] = None,
    ) -> Optional[str]:
        # 创建临时输入/输出目录，文件名改成规范ASCII，避免特殊字符问题
        tmp_root = tempfile.mkdtemp(prefix="nnunet_case_")
        tmp_in = os.path.join(tmp_root, "in")
        tmp_out = os.path.join(tmp_root, "out")
        os.makedirs(tmp_in, exist_ok=True)
        os.makedirs(tmp_out, exist_ok=True)

        # 拷贝并按通道重命名为 case_0000.nii.gz, case_0001...；若只有1个文件也命名为 _0000
        base = "case"
        for ch, src in enumerate(file_list):
            ext = ".nii.gz" if src.lower().endswith(".nii.gz") else ".nii"
            dst = os.path.join(tmp_in, f"{base}_{ch:04d}{ext}")
            self._safe_copy(src, dst)

        # 运行并解析单例进度
        log_file_path = os.path.join(tmp_out, f"{base}_progress.log")

        def _emit(pct: int, line: str):
            if on_progress:
                try:
                    on_progress(max(0, min(100, int(pct))), line)
                except Exception:
                    pass

        try:
            command = self._build_predict_command(tmp_in, tmp_out, tag_id, config, folds)
            with open(log_file_path, "a", encoding="utf-8", errors="ignore") as logf:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                )
                last = -1
                last_line = None
                last_emit_time = 0.0
                throttle_ms = 200
                if proc.stdout is not None:
                    for line in proc.stdout:
                        try:
                            logf.write(line)
                        except Exception:
                            pass
                        m = re.search(r"(\d{1,3})%", line)
                        pct = None
                        if m:
                            pct = int(m.group(1))
                        else:
                            m2 = re.search(r"\b(\d+)\s*/\s*(\d+)\b", line)
                            if m2:
                                a, b = int(m2.group(1)), int(m2.group(2))
                                if b > 0 and a <= b <= 10000:
                                    pct = int(a * 100 / b)
                        now = time.time() * 1000.0
                        should_emit = False
                        if pct is not None and pct != last:
                            last = pct
                            should_emit = True
                        else:
                            if line != last_line or (now - last_emit_time) >= throttle_ms:
                                should_emit = True
                        if should_emit:
                            _emit(last if last >= 0 else 0, line)
                            last_emit_time = now
                            last_line = line
                ret = proc.wait()
                if ret != 0:
                    raise subprocess.CalledProcessError(ret, command)

            # 移动结果到最终输出，使用原case_id命名
            pred_file = self._find_pred_file(tmp_out)
            if not pred_file:
                raise RuntimeError("未找到预测输出文件")
            ext = ".nii.gz" if pred_file.lower().endswith(".nii.gz") else ".nii"
            final_path = os.path.join(final_output_dir, f"{case_id}{ext}")
            self._safe_copy(pred_file, final_path)
            return final_path
        finally:
            # 清理临时目录
            try:
                import shutil
                shutil.rmtree(tmp_root, ignore_errors=True)
            except Exception:
                pass
        return None

    def collect_cases(self, images_path: str) -> List[Tuple[str, List[str]]]:
        return self._collect_cases(images_path)

    def _safe_copy(self, src: str, dst: str) -> None:
        # 避免路径中的特殊字符造成shell转义问题，使用Python拷贝
        import shutil

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    def _find_pred_file(self, out_dir: str) -> Optional[str]:
        # 在输出目录中寻找第一个 .nii 或 .nii.gz 文件
        candidates: List[str] = []
        for name in os.listdir(out_dir):
            lower = name.lower()
            if lower.endswith(".nii.gz") or lower.endswith(".nii"):
                candidates.append(os.path.join(out_dir, name))
        candidates.sort()
        return candidates[0] if candidates else None
