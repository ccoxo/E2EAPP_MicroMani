# 阅读导航 03｜后端契约与配置
# 职责：显式转换操作者侧与硬件侧；夹爪主手来源也经此映射，避免直接按名称配对。
# 先看：hardware_side_for_operator_side → operator_side_for_hardware_side → operator_gripper_source_for_side → gripper_source_for_hardware_side。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

from __future__ import annotations

from typing import Literal

SideName = Literal["left", "right"]


# 操作者面对机构时，画面左侧对应历史硬件 right；这是显示坐标转换，不是重新接线或交换控制卡。
def hardware_side_for_operator_side(side: SideName) -> SideName:
    return "right" if side == "left" else "left"


def operator_side_for_hardware_side(side: SideName) -> SideName:
    return "right" if side == "left" else "left"


def operator_gripper_source_for_side(side: SideName) -> str:
    return "PhysicalLeft" if side == "left" else "PhysicalRight"


def gripper_source_for_hardware_side(side: SideName) -> str:
    operator_side = operator_side_for_hardware_side(side)
    return operator_gripper_source_for_side(operator_side)
