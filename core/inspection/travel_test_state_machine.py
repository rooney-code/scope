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
        self._planned_directions: list[TravelDirection] = []  # 재시험 시 남은 미완료 방향 복원용
        self.current_direction: TravelDirection | None = None
        self.phase: Phase = Phase.IDLE
        self.overall_verdict: Verdict = Verdict.IN_PROGRESS
        self.finalized: bool = False  # "시험 종료"(DB 저장) 버튼을 누르면 True - 이후 재시험 불가

        self.direction_results: list[DirectionTestResult] = []
        self._attempt_counters: dict[TravelDirection, int] = {}

        self._stability = self._stability_factory()
        self._max_primary_reached: float = 0.0
        self._max_abs_cross: float = 0.0
        self._last_primary: float = 0.0
        self._last_cross: float = 0.0
        self._pending_checks: list[CheckResult] = []
        self._dead_click_flagged: bool = False

    # ---- 큐/방향 관리 ----
    def configure(self, directions: list[TravelDirection]) -> None:
        if self.phase not in (Phase.IDLE, Phase.INSPECTION_DONE):
            raise RuntimeError("검사가 진행 중일 때는 방향 큐를 재구성할 수 없습니다. abort 또는 완료 후 사용하세요.")
        self.direction_queue = list(directions)
        self._planned_directions = list(directions)
        self.current_direction = None
        self.phase = Phase.IDLE
        self.overall_verdict = Verdict.IN_PROGRESS
        self.finalized = False
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
        self.direction_queue = []
        self._planned_directions = []
        self.current_direction = None
        self.direction_results = []
        self._attempt_counters = {}
        self.overall_verdict = Verdict.IN_PROGRESS
        self.finalized = False
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

    def retest_direction(self, direction: TravelDirection) -> None:
        """이미 완료된(합격/불량 무관) 방향을 재시험한다 - 작업자의 조작 실수로 기록이 이상해
        보일 때 사용. abort와 달리 "이미 끝난" 방향을 대상으로 하며, 그 방향의 이전 기록은
        즉시 초기화(폐기)되고 재시험 결과로 대체된다(이력 보존 없음 - 아직 DB에 저장되지 않은
        상태이므로 그냥 지워도 됨. finalize() 이후에는 재시험 불가).

        stop_on_failure_scope="entire_inspection"으로 검사 전체가 먼저 종료된 상태였다면,
        아직 시도하지 못한 나머지 방향들도 함께 큐에 복원해 이어서 진행할 수 있게 한다.
        """
        if self.finalized:
            raise RuntimeError("이미 시험 종료(저장)된 검사는 재시험할 수 없습니다.")
        if self.phase not in (Phase.DIRECTION_DONE, Phase.INSPECTION_DONE):
            raise RuntimeError("진행 중인 방향이 있을 때는 재시험을 시작할 수 없습니다 (먼저 종료하세요).")
        if not any(r.direction == direction for r in self.direction_results):
            raise RuntimeError(f"{direction}는 완료된 적이 없어 재시험할 수 없습니다.")

        self.direction_results = [r for r in self.direction_results if r.direction != direction]
        attempted = {r.direction for r in self.direction_results}
        remaining = [d for d in self._planned_directions if d not in attempted and d != direction]
        self.direction_queue = [direction, *remaining]
        self.overall_verdict = Verdict.IN_PROGRESS
        self.phase = Phase.DIRECTION_DONE  # start_next_direction()으로 바로 이어서 시작 가능

    # ---- 최종 확정(DB 저장 게이트) ----
    @property
    def is_ready_to_finalize(self) -> bool:
        return self.phase == Phase.INSPECTION_DONE and not self.finalized

    def finalize(self) -> None:
        """'시험 종료' 버튼 - 지금까지의 결과를 확정한다. 이후에는 재시험이 불가능하며,
        UI/리포지토리는 이 시점의 direction_results/overall_verdict를 DB에 저장한다."""
        if not self.is_ready_to_finalize:
            raise RuntimeError("아직 모든 방향이 끝나지 않았거나 이미 종료되었습니다.")
        self.finalized = True

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
        if not state.is_stable:
            return

        # "이동 완료"/"원점 복귀 완료" 버튼 없이도, 작업자가 실제로 하던 대로(목표 근처에서
        # 잠깐 멈춰 눈금 확인 -> 후진 -> 원점 근처에서 잠깐 멈춰 백래쉬 확인) 진행하면 그
        # 멈춤을 감지해 자동으로 평가한다. 이동 중간에 잠깐 멈추는 것(예: 손 위치 고쳐잡기)은
        # 무시되도록, OUTBOUND는 목표 도달 여부로, RETURN은 원점 근접 여부로 게이팅한다.
        #
        # mark_far_point_reached()/mark_returned_to_origin()는 그대로 공개 API로 남겨둔다 -
        # 기계적 한계로 목표에 못 미쳐 자동 감지가 안 되는 경우(실측 사례) 작업자가 직접
        # "여기까지가 한계"라고 수동으로 확정할 수 있는 유일한 경로이기 때문.
        if self.phase == Phase.OUTBOUND:
            if self._max_primary_reached >= self.settings.travel_target_moa - self.MEASUREMENT_EPSILON_MOA:
                self.mark_far_point_reached()
        elif self.phase == Phase.RETURN:
            # near_zero_band_moa는 "0에 정확히 온 상태"가 아니라 "복귀를 마쳤다고 판단할 수
            # 있는 근방"을 뜻함 - 백래쉬 자체가 0에서 떨어져 있는 걸 재는 값이므로, 이 밴드가
            # 너무 좁으면(예: backlash_threshold_moa보다 좁으면) 실제 백래쉬 불량을 자동으로
            # 잡아내지 못하게 된다. 도중에 잠깐 멈추는 것과는 충분히 구분되는 값으로 설정할 것.
            if abs(self._last_primary) <= self.settings.near_zero_band_moa:
                self.mark_returned_to_origin()

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

        # mark_far_point_reached()는 항상 목표 근처에서 안정된 직후(자동 감지) 또는 작업자가
        # 그 상태를 보면서 직접(수동) 호출하므로, 호출 시점의 _last_cross가 곧 "정착된" 값이다.
        drift_measured = abs(self._last_cross)
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
