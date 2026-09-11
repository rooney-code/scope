"""검사 도메인 모델 (dataclass/enum)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TravelDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class CheckType(str, Enum):
    TRAVEL_AMOUNT = "travel_amount"
    DEAD_CLICK = "dead_click"
    DRIFT = "drift"
    SHIFT = "shift"
    BACKLASH = "backlash"


class Verdict(str, Enum):
    PASS = "합격"
    FAIL = "불량"
    IN_PROGRESS = "진행중"


@dataclass
class CheckResult:
    check_type: CheckType
    measured_value: float | None
    threshold_used: float | None
    status: Verdict


@dataclass
class DirectionTestResult:
    direction: TravelDirection
    attempt_number: int
    verdict: Verdict
    check_results: list[CheckResult] = field(default_factory=list)


@dataclass
class InspectionSession:
    scope_id: str
    operator: str = ""
    direction_results: list[DirectionTestResult] = field(default_factory=list)
    overall_verdict: Verdict = Verdict.IN_PROGRESS


# 방향별 주축/교차축 정의 (계획서 표 참고)
# primary_sign: 해당 방향으로 "바깥으로" 이동할 때 주축 값이 양(+)이 되도록 하는 부호
#   up   -> primary = +y_moa (교차축 = x_moa)
#   down -> primary = -y_moa (교차축 = x_moa)
#   left -> primary = -x_moa (교차축 = y_moa)
#   right-> primary = +x_moa (교차축 = y_moa)
_AXIS_MAP: dict[TravelDirection, dict] = {
    TravelDirection.UP: {"primary_axis": "y", "primary_sign": 1.0, "cross_axis": "x"},
    TravelDirection.DOWN: {"primary_axis": "y", "primary_sign": -1.0, "cross_axis": "x"},
    TravelDirection.LEFT: {"primary_axis": "x", "primary_sign": -1.0, "cross_axis": "y"},
    TravelDirection.RIGHT: {"primary_axis": "x", "primary_sign": 1.0, "cross_axis": "y"},
}


def resolve_primary_and_cross(direction: TravelDirection, x_moa: float, y_moa: float) -> tuple[float, float]:
    """(x_moa, y_moa) 샘플을 해당 방향의 (주축 값, 교차축 값)으로 변환.

    주축 값은 "바깥으로 이동"이 양수가 되도록 부호를 맞춘다. 교차축 값은 원래 부호 그대로
    (드리프트/쉬프트/백래쉬 판정 시 절대값을 취함).
    """
    axis_info = _AXIS_MAP[direction]
    primary_raw = x_moa if axis_info["primary_axis"] == "x" else y_moa
    cross_raw = y_moa if axis_info["primary_axis"] == "x" else x_moa
    primary = primary_raw * axis_info["primary_sign"]
    return primary, cross_raw
