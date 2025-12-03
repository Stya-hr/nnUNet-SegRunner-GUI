from dataclasses import dataclass
from enum import Enum
from typing import Optional
import datetime


class TaskStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass
class Task:
    images_path: str
    # 用户希望的输出目录（作为nnUNet的 -o 参数）。若为空，则使用默认：与输入同级的seg目录
    desired_output_dir: Optional[str] = None
    output_path: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    error_message: Optional[str] = None
    started_at: Optional[datetime.datetime] = None
    finished_at: Optional[datetime.datetime] = None
