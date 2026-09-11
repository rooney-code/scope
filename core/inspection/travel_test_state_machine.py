"""2단계 트래블 검사 상태기계.

작업자가 선택한 방향 큐(기본 상->하->좌->우)를 순서대로 실행한다. 방향별로 5가지 검사
(이동량/데드클릭/드리프트/쉬프트/백래쉬)를 순서대로 확인하며, 하나라도 불량이면 즉시 해당
방향 시험을 종료한다. "시험 중지"는 언제든 가능하며 호출 시 현재 시도를 완전히 폐기한다
(결과 저장 안 함).

축 정의: models.resolve_primary_and_cross()를 통해 방향별 주축/교차축을 일관되게 계산.
"""
from __future__ import annotations

from enum import Enum, auto

from core.config.settings import Stage2Settings
from core.inspection.models import (
    CheckResult,
    CheckType,
    DirectionTestResult,
    TravelDirection,
    Verdict,
    resolve_primary_and_cross,
)
from core.tracking.position_sample import PositionSample
from core.tracking.stability_detector import StabilityDetector


class Phase(Enum):
    IDLE = auto()  # 방향 큐 대기 (아직 시작 안 함)
    OUTBOUND = auto()  # 0 -> 목표(35moa 부근)로 이동 중
    RETURN = auto()  # 목표 -> 0 복귀 중
    DIRECTION_DONE = auto()  # 현재 방향 완료(합격/불량), 다음 방향 대기
    INSPECTION_DONE = auto()  # 전체 검사 종료


class TravelTestStateMachine:
    # 카메라/픽셀 양자화로 인한 측정 잡음 보정용 - 판정 임계값(threshold_used로 기록되는 값)과는
    # 무관한 순수 수치 안정성 여유값. 예: 35.0 목표에 34.997처럼 근소하게 못 미치는 것을
    # 기계적 이동량 부족으로 오판하지 않기 위함.
    MEASUREMENT_EPSILON_MOA = 0.1

    def __init__(self, settings: Stage2Settings, stability_detector_factory=None) -> None:
        self.settings = settings
        self._stability_factory = stability_detector_factory or (
            lambda: StabilityDetector(window_size_samples=8, variance_threshold_moa2=0.01, min_stable_duration_ms=150)
        )

        self.direction_queue: list[TravelDirection] = []
        self.current_direction: TravelDirection | None = None
        self.phase: Phase = Phase.IDLE
        self.overall_verdict: Verdict = Verdict.IN_PROGRESS

        self.direction_results: list[DirectionTestResult] = []
        self._attempt_counters: dict[TravelDirection, int] = {}

        self._stability = self._stability_factory()
        self._max_primary_reached: float = 0.0
        self._max_abs_cross: float = 0.0
        self._last_primary: float = 0.0
        self._last_cross: float = 0.0
        self._drift_value: float | None = None
        self._pending_checks: list[CheckResult] = []
        self._dead_click_flagged: bool = False

    # ---- 큐/방향 관리 ----
    def configure(self, directions: list[TravelDirection]) -> None:
        if self.phase not in (Phase.IDLE, Phase.INSPECTION_DONE):
            raise RuntimeError("검사가 진행 중일 때는 방향 큐를 재구성할 수 없습니다. abort 또는 완료 후 사용하세요.")
        self.direction_queue = list(directions)
        self.current_direction = None
        self.phase = Phase.IDLE
        self.overall_verdict = Verdict.IN_PROGRESS
        self.direction_results = []

    def has_next_direction(self) -> bool:
        return len(self.direction_queue) > 0

    def start_next_direction(self) -> TravelDirection:
        if self.phase not in (Phase.IDLE, Phase.DIRECTION_DONE):
            raise RuntimeError(f"현재 phase({self.phase})에서는 다음 방향을 시작할 수 없습니다.")
        if not self.direction_queue:
            self.phase = Phase.INSPECTION_DONE
            raise RuntimeError("더 이상 진행할 방향이 없습니다.")
        self.current_direction = self.direction_queue.pop(0)
        self._reset_direction_buffers()
        self.phase = Phase.OUTBOUND
        return self.current_direction

    def restart_current_direction(self) -> None:
        """현재 방향을 처음부터 재시작 (교정 등 조치 후). 진행 중 버퍼만 초기화."""
        if self.current_direction is None:
            raise RuntimeError("재시작할 현재 방향이 없습니다.")
        self._reset_direction_buffers()
        self.phase = Phase.OUTBOUND

    def restart_all(self) -> None:
        """전체 검사를 처음부터 재시작 (같은 scope 기준, 방향 큐/결과 모두 초기화)."""
        all_directions = [r.direction for r in self.direction_results] if not self.direction_queue else None
        self.direction_queue = []
        self.current_direction = None
        self.direction_results = []
        self._attempt_counters = {}
        self.overall_verdict = Verdict.IN_PROGRESS
        self.phase = Phase.IDLE

    def abort_current_direction(self) -> None:
        """'시험 중지' - 현재 시도를 완전히 폐기한다 (결과 미저장). 같은 방향을 재시작할 수 있게
        current_direction은 유지하고 phase만 IDLE 상당으로 되돌린다."""
        if self.current_direction is None:
            raise RuntimeError("중지할 진행 중인 방향이 없습니다.")
        self._reset_direction_buffers()
        self.phase = Phase.DIRECTION_DONE  # 대기 상태. UI는 restart_current_direction() 또는 다음 방향 진행 선택 가능
        # 방향 큐 맨 앞에 다시 넣어 재시작 대상으로 유지
        if self.current_direction not in self.direction_queue:
            self.direction_queue.insert(0, self.current_direction)
        self.current_direction = None

    # ---- 실시간 위치 피드 ----
    def feed_position(self, sample: PositionSample) -> None:
        if self.phase not in (Phase.OUTBOUND, Phase.RETURN) or self.current_direction is None:
            return

        primary, cross = resolve_primary_and_cross(self.current_direction, sample.x_moa, sample.y_moa)
        self._last_primary = primary
        self._last_cross = cross

        if self.phase == Phase.OUTBOUND:
            self._max_primary_reached = max(self._max_primary_reached, primary)
            self._max_abs_cross = max(self._max_abs_cross, abs(cross))

        # StabilityDetector는 (주축, 교차축)을 (x_moa, y_moa) 슬롯에 재사용해서 먹인다
        stability_sample = PositionSample(timestamp_s=sample.timestamp_s, x_moa=primary, y_moa=cross)
        state = self._stability.feed(stability_sample)
        if state.is_stable and self.phase == Phase.OUTBOUND and self._drift_value is None:
            # 목표 부근에서 안정되면 그 순간의 교차축 값을 드리프트로 기록
            self._drift_value = state.stable_position[1]

    # ---- 작업자 액션 ----
    def flag_dead_click(self) -> None:
        """작업자가 데드클릭 발생을 직접 판단/입력. 다른 검사와 무관하게 즉시 불량 처리."""
        self._dead_click_flagged = True
        self._finalize_direction(
            [
                CheckResult(
                    check_type=CheckType.DEAD_CLICK,
                    measured_value=None,
                    threshold_used=None,
                    status=Verdict.FAIL,
                )
            ],
            Verdict.FAIL,
        )

    def mark_far_point_reached(self) -> None:
        """작업자가 목표(약 35MOA) 부근까지 이동을 완료했음을 알림 ('이동 완료' 버튼).

        이동량/쉬프트/드리프트를 순서대로 평가하고, 모두 통과하면 복귀(RETURN) 단계로 전환한다.
        """
        if self.phase != Phase.OUTBOUND:
            raise RuntimeError("이동 완료는 OUTBOUND 단계에서만 호출할 수 있습니다.")

        s = self.settings
        checks: list[CheckResult] = []

        travel_ok = self._max_primary_reached >= s.travel_target_moa - self.MEASUREMENT_EPSILON_MOA
        checks.append(
            CheckResult(
                check_type=CheckType.TRAVEL_AMOUNT,
                measured_value=self._max_primary_reached,
                threshold_used=s.travel_target_moa,
                status=Verdict.PASS if travel_ok else Verdict.FAIL,
            )
        )
        if not travel_ok:
            self._finalize_direction(checks, Verdict.FAIL)
            return

        shift_ok = self._max_abs_cross <= s.shift_threshold_moa
        checks.append(
            CheckResult(
                check_type=CheckType.SHIFT,
                measured_value=self._max_abs_cross,
                threshold_used=s.shift_threshold_moa,
                status=Verdict.PASS if shift_ok else Verdict.FAIL,
            )
        )
        if not shift_ok:
            self._finalize_direction(checks, Verdict.FAIL)
            return

        drift_measured = abs(self._drift_value) if self._drift_value is not None else abs(self._last_cross)
        drift_ok = drift_measured <= s.drift_threshold_moa
        checks.append(
            CheckResult(
                check_type=CheckType.DRIFT,
                measured_value=drift_measured,
                threshold_used=s.drift_threshold_moa,
                status=Verdict.PASS if drift_ok else Verdict.FAIL,
            )
        )
        if not drift_ok:
            self._finalize_direction(checks, Verdict.FAIL)
            return

        # 모두 통과 -> 복귀 단계로 전환 (백래쉬는 원점 복귀 시 평가)
        self._pending_checks = checks
        self._stability.reset()
        self.phase = Phase.RETURN

    def mark_returned_to_origin(self) -> None:
        """작업자가 원점(0)으로 복귀를 완료했음을 알림. 백래쉬를 평가하고 방향을 종료한다."""
        if self.phase != Phase.RETURN:
            raise RuntimeError("원점 복귀 확인은 RETURN 단계에서만 호출할 수 있습니다.")

        s = self.settings
        backlash_measured = abs(self._last_primary)
        backlash_ok = backlash_measured <= s.backlash_threshold_moa
        checks = list(self._pending_checks)
        checks.append(
            CheckResult(
                check_type=CheckType.BACKLASH,
                measured_value=backlash_measured,
                threshold_used=s.backlash_threshold_moa,
                status=Verdict.PASS if backlash_ok else Verdict.FAIL,
            )
        )
        self._finalize_direction(checks, Verdict.PASS if backlash_ok else Verdict.FAIL)

    # ---- 내부 ----
    def _reset_direction_buffers(self) -> None:
        self._stability = self._stability_factory()
        self._max_primary_reached = 0.0
        self._max_abs_cross = 0.0
        self._last_primary = 0.0
        self._last_cross = 0.0
        self._drift_value = None
        self._pending_checks = []
        self._dead_click_flagged = False

    def _finalize_direction(self, checks: list[CheckResult], verdict: Verdict) -> None:
        direction = self.current_direction
        assert direction is not None

        attempt_number = self._attempt_counters.get(direction, 0) + 1
        self._attempt_counters[direction] = attempt_number

        result = DirectionTestResult(
            direction=direction, attempt_number=attempt_number, verdict=verdict, check_results=checks
        )
        self.direction_results.append(result)

        self.current_direction = None
        self.phase = Phase.DIRECTION_DONE

        if verdict == Verdict.FAIL and self.settings.stop_on_failure_scope == "entire_inspection":
            self.direction_queue = []
            self.overall_verdict = Verdict.FAIL
            self.phase = Phase.INSPECTION_DONE
        elif not self.direction_queue:
            self.overall_verdict = (
                Verdict.PASS
                if all(r.verdict == Verdict.PASS for r in self.direction_results)
                else Verdict.FAIL
            )
            self.phase = Phase.INSPECTION_DONE
