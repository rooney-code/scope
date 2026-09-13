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
    measured_value: float | None  # 시작 지점(start_point_moa) 기준으로 보정된 최종값 - 판정에 사용
    threshold_used: float | None
    status: Verdict
    # 보정 전(그리드 절대 원점 기준) 원시 측정값 - 참고/감사용, 판정에는 쓰이지 않음.
    # 사용자 확인 사항(2026-09-13): 시험 시작점이 그리드 원점(0,0)과 정확히 일치할 가능성은
    # 낮으므로(예: 실제로는 (0.1, 0.1)에서 시작), 판정은 항상 시작 지점 기준 상대값으로 해야
    # 한다 - docs/detection_notes.md 12차 참고.
    raw_measured_value: float | None = None


@dataclass
class DirectionTestResult:
    direction: TravelDirection
    attempt_number: int
    verdict: Verdict
    check_results: list[CheckResult] = field(default_factory=list)
    # 이 방향 시험을 시작한 시점의 실측 좌표(그리드 절대 좌표, MOA) - 모든 CheckResult의
    # measured_value가 이 지점을 기준(0,0)으로 재계산된 상대값임을 감사할 수 있도록 기록.
    start_point_moa: tuple[float, float] | None = None


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
