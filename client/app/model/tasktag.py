from dataclasses import dataclass
from typing import Optional, Dict


@dataclass
class TaskTagSpec:
    """
    描述nnUNet任务标签与相关推理配置。
    - id: 对应`nnUNetv2_predict -d`的数据集/任务编号
    - config: 对应`-c`配置（默认3d_fullres）
    - folds: 对应`-f`折号（默认0）
    - name: 可读名称（可选）
    """
    id: str
    config: str = "3d_fullres"
    folds: str = "0"
    name: Optional[str] = None


# 预置示例：IO分割任务
IO_SPLIT = TaskTagSpec(id="101", name="IO Split")
PRE_SEG = TaskTagSpec(id="007", name="Pre Seg")

# 任务标签预设集合（供GUI下拉使用）
PRESET_TASK_TAGS: Dict[str, TaskTagSpec] = {
    "IO Split (101)": TaskTagSpec(id="101", config="3d_fullres", folds="0", name="IO Split"),
    "Pre Seg (007)": TaskTagSpec(id="007", config="3d_fullres", folds="0", name="Pre Seg"),
}
