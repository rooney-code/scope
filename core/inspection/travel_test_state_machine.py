"""2단계 트래블 검사 상태기계.

작업자가 선택한 방향 큐(기본 상->하->좌->우)를 순서대로 실행한다. 방향별로 5가지 검사
(이동량/데드클릭/드리프트/쉬프트/백래쉬)를 순서대로 확인하며, 하나라도 불량이면 즉시 해당
방향 시험을 종료한다. "시험 중지"는 언제든 가능하며 호출 시 현재 시도를 완전히 폐기한다
(결과 저장 안 함).

축 정의: models.resolve_primary_and_cross()를 통해 방향별 주축/교차축을 일관되게 계산.
"""
from __future__ import annotations

import time
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
            lambda: StabilityDetector(window_size_samples=8, variance_threshold_moa2=0.01, min_stable_duration_ms=3000)
        )

        self.direction_queue: list[TravelDirection] = []
        # 방향별 박스 UI(start_direction())는 순서 큐 없이 4방향을 언제든 자유롭게 시작하므로,
        # 기본값은 항상 4방향 전체 - configure()를 명시적으로 호출하면(기존 순서 큐 흐름) 그
        # 목록으로 덮어써진다.
        self._planned_directions: list[TravelDirection] = list(TravelDirection)
        self.current_direction: TravelDirection | None = None
        self.phase: Phase = Phase.IDLE
        self.overall_verdict: Verdict = Verdict.IN_PROGRESS
        self.finalized: bool = False  # "시험 종료"(DB 저장) 버튼을 누르면 True - 이후 재시험 불가
        # True: configure()+start_next_direction()의 "순서 큐" 흐름(기존 테스트가 검증하는
        # 동작 - 실패 시 stop_on_failure_scope에 따라 즉시 전체 종료 가능).
        # False: start_direction()으로 어떤 순서로든 자유롭게 진행하는 방향별 박스 UI 흐름 -
        # 한 방향의 불량이 다른 방향 진행을 막지 않고, 계획된 방향이 모두 시도되면 종료로 간주.
        self._sequential_queue_mode: bool = True

        self.direction_results: list[DirectionTestResult] = []
        self._attempt_counters: dict[TravelDirection, int] = {}

        self._stability = self._stability_factory()
        self._max_primary_reached: float = 0.0
        self._max_abs_cross: float = 0.0
        self._last_primary: float = 0.0
        self._last_cross: float = 0.0
        # 그리드 절대 원점 기준 원시값(raw_measured_value 기록용) - 아래 baseline 관련 필드와
        # 함께 사용. 판정 자체는 항상 baseline으로 보정된 위 필드들을 사용한다.
        self._max_primary_reached_raw: float = 0.0
        self._max_abs_cross_raw: float = 0.0
        self._last_primary_raw: float = 0.0
        self._last_cross_raw: float = 0.0
        # 이 방향 시험을 시작한 시점(첫 feed_position() 샘플)의 실측 좌표 - 시험 시작점이
        # 그리드 절대 원점(0,0)과 정확히 일치할 가능성은 낮으므로(사용자 확인 사항,
        # 2026-09-13), 이후 모든 primary/cross 값은 이 시작점을 기준(0,0)으로 재계산된
        # 상대값을 사용한다. docs/detection_notes.md 12차 참고.
        self._baseline_captured: bool = False
        self._baseline_x_moa: float = 0.0
        self._baseline_y_moa: float = 0.0
        self._baseline_primary: float = 0.0
        self._baseline_cross: float = 0.0
        self._pending_checks: list[CheckResult] = []

        # 대기 중(IDLE/DIRECTION_DONE, 진행 중인 방향 없음) 안정성 추적 - 진행 중 방향의
        # self._stability와 별개 인스턴스. 원점 부근에서 안정적으로 멈춰 있던 마지막 위치를
        # 계속 갱신해두었다가, 거기서 한 축으로 auto_direction_threshold_moa 이상 벗어나면
        # 그 위치를 baseline 삼아 자동으로 해당 방향 시험을 시작한다(사용자 요청, 2026-09-16 -
        # 매 방향마다 버튼을 누르는 번거로움을 줄이기 위함). _feed_idle() 참고.
        self._idle_stability = self._stability_factory()
        self._idle_baseline: tuple[float, float] | None = None

    # ---- 큐/방향 관리 ----
    def configure(self, directions: list[TravelDirection]) -> None:
        if self.phase not in (Phase.IDLE, Phase.INSPECTION_DONE):
            raise RuntimeError("검사가 진행 중일 때는 방향 큐를 재구성할 수 없습니다. abort 또는 완료 후 사용하세요.")
        self.direction_queue = list(directions)
        self._planned_directions = list(directions)
        self._sequential_queue_mode = True
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

    def start_direction(self, direction: TravelDirection) -> TravelDirection:
        """방향별 박스의 "시작/재시작" 버튼 - 순서 큐와 무관하게, 어떤 방향이든 언제든 (재)시작
        한다. 동시에 두 방향을 진행할 수 없고 재시작 전에는 항상 원점으로 이동해야 하므로,
        다른 방향이 진행 중이었다면 그 미완성 기록은 그대로 버려진다(사용자 확인: 부분 기록은
        의미가 없음 - 저장되지 않음). 이미 완료된 방향이면 그 기록도 지우고 재시험으로 취급한다
        (retest_direction()과 동일 정책). 이후 이 인스턴스는 "자유 순서" 모드로 동작해,
        stop_on_failure_scope와 무관하게 한 방향의 불량이 다른 방향 시작을 막지 않는다."""
        if self.finalized:
            raise RuntimeError("이미 시험 종료(저장)된 검사는 다시 시작할 수 없습니다.")
        if self.current_direction is not None and self.current_direction != direction:
            self._reset_direction_buffers()  # 다른 방향의 미완성 진행분은 그냥 버림(기록 없음)

        self.direction_results = [r for r in self.direction_results if r.direction != direction]
        if direction not in self._planned_directions:
            self._planned_directions.append(direction)
        if direction in self.direction_queue:
            self.direction_queue.remove(direction)

        self._sequential_queue_mode = False
        self.current_direction = direction
        self._reset_direction_buffers()
        self.phase = Phase.OUTBOUND
        self.overall_verdict = Verdict.IN_PROGRESS
        return direction

    def restart_current_direction(self) -> None:
        """현재 방향을 처음부터 재시작 (교정 등 조치 후). 진행 중 버퍼만 초기화."""
        if self.current_direction is None:
            raise RuntimeError("재시작할 현재 방향이 없습니다.")
        self._reset_direction_buffers()
        self.phase = Phase.OUTBOUND

    def restart_all(self) -> None:
        """전체 검사를 처음부터 재시작 (같은 scope 기준, 방향 큐/결과 모두 초기화)."""
        self.direction_queue = []
        self._planned_directions = list(TravelDirection)
        self._sequential_queue_mode = True
        self.current_direction = None
        self.direction_results = []
        self._attempt_counters = {}
        self.overall_verdict = Verdict.IN_PROGRESS
        self.finalized = False
        self.phase = Phase.IDLE
        self._reset_idle_tracking()

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
        """시험 종료(결과 확정) 가능 여부 - 원래는 4방향을 전부 시도해야(INSPECTION_DONE)만
        가능했는데, 불량이 나거나 사용자가 일부만 하고 끝내고 싶을 때도 종료할 수 있어야
        한다는 요청(2026-09-16)에 따라 "지금 진행 중인 방향이 없고(OUTBOUND/RETURN이
        아니고) 완료된 방향이 하나 이상"이면 언제든 종료 가능하게 완화했다."""
        return (
            not self.finalized
            and self.phase not in (Phase.OUTBOUND, Phase.RETURN)
            and bool(self.direction_results)
        )

    def finalize(self) -> None:
        """'시험 종료' 버튼 - 지금까지의 결과를 확정한다. 이후에는 재시험이 불가능하며,
        UI/리포지토리는 이 시점의 direction_results/overall_verdict를 DB에 저장한다."""
        if not self.is_ready_to_finalize:
            raise RuntimeError("진행 중인 방향이 있거나 완료된 방향이 없거나 이미 종료되었습니다.")
        # 4방향을 다 못 채우고(일부만) 종료하는 경우 overall_verdict가 아직 IN_PROGRESS일 수
        # 있으므로(원래는 INSPECTION_DONE 전환 시점에만 계산됐음) 여기서 확정 직전에 다시
        # 계산해둔다.
        self.overall_verdict = (
            Verdict.PASS if all(r.verdict == Verdict.PASS for r in self.direction_results) else Verdict.FAIL
        )
        self.finalized = True

    # ---- 실시간 표시용 읽기 전용 접근자 (UI 라이브 표시 + 텍스트 리포트) ----
    @property
    def baseline_moa(self) -> tuple[float, float] | None:
        """현재 진행 중인 방향 시험의 시작 위치(그리드 절대 MOA 좌표) - 아직 시작 전이면 None."""
        return (self._baseline_x_moa, self._baseline_y_moa) if self._baseline_captured else None

    @property
    def last_primary_moa(self) -> float:
        """baseline 기준 상대 주축 값(가장 최근 샘플) - 바깥으로 이동이 양수."""
        return self._last_primary

    @property
    def last_abs_cross_moa(self) -> float:
        """baseline 기준 상대 교차축 값(가장 최근 샘플)의 절대값."""
        return abs(self._last_cross)

    @property
    def max_primary_reached_moa(self) -> float:
        return self._max_primary_reached

    @property
    def max_abs_cross_moa(self) -> float:
        return self._max_abs_cross

    @property
    def idle_wait_remaining_s(self) -> float | None:
        """대기 baseline(원점 정렬) 안정성 카운트다운까지 남은 시간(초) - 아직 추적을 시작
        못했으면(방금 리셋됨/윈도우 미충족) None. UI가 "원점 정렬 대기" 메시지에 남은 초를
        표시하는 데 쓴다(사용자 요청, 2026-09-16)."""
        return self._idle_stability.remaining_hold_s(time.time())

    @property
    def direction_wait_remaining_s(self) -> float | None:
        """현재 진행 중인 방향의 정지 판정(목표 도달/원점 복귀 확인) 카운트다운까지 남은
        시간(초) - idle_wait_remaining_s와 동일한 용도, 방향 진행 중(OUTBOUND/RETURN) 버전."""
        return self._stability.remaining_hold_s(time.time())

    @property
    def idle_baseline_captured(self) -> bool:
        """방향 진행 중이 아닐 때(대기 중), 레드닷이 한 위치에서 실제로 안정적으로
        머물렀다고 판단해 "원점 정렬 완료" 상태로 볼 수 있는지 - "대기하세요"와 "이동을
        감지하면"이 한 문장에 섞여 모순처럼 읽힌다는 지적(2026-09-16)에 따라, UI가 "아직
        정렬 대기 중"과 "정렬 완료, 이제 이동해도 됨"을 명확히 구분된 두 단계로 안내할 수
        있게 이 값을 노출한다. _feed_idle()의 _idle_baseline과 동일한 기준(대기 안정성
        StabilityDetector가 min_stable_duration_ms 동안 낮은 분산을 확인)이다."""
        return self._idle_baseline is not None

    # ---- 실시간 위치 피드 ----
    def feed_position(self, sample: PositionSample) -> None:
        if self.finalized:
            return
        if self.phase in (Phase.IDLE, Phase.DIRECTION_DONE) and self.current_direction is None:
            self._feed_idle(sample)
            if self.current_direction is None:
                return
            # 자동으로 방향이 시작됐으면(_feed_idle 참고) 리턴하지 않고 이어서 이 샘플을
            # 그 방향의 첫 OUTBOUND 샘플로 바로 처리한다.
        elif self.phase not in (Phase.OUTBOUND, Phase.RETURN) or self.current_direction is None:
            return

        primary_raw, cross_raw = resolve_primary_and_cross(self.current_direction, sample.x_moa, sample.y_moa)

        if not self._baseline_captured:
            # 이 방향 시험의 실제 시작 지점을 baseline으로 확정 - 이후 모든 판정은 그리드
            # 절대 원점이 아니라 이 지점을 기준으로 계산된다.
            self._baseline_x_moa = sample.x_moa
            self._baseline_y_moa = sample.y_moa
            self._baseline_primary = primary_raw
            self._baseline_cross = cross_raw
            self._baseline_captured = True

        primary = primary_raw - self._baseline_primary
        cross = cross_raw - self._baseline_cross

        self._last_primary = primary
        self._last_cross = cross
        self._last_primary_raw = primary_raw
        self._last_cross_raw = cross_raw

        if self.phase == Phase.OUTBOUND:
            if primary > self._max_primary_reached:
                self._max_primary_reached = primary
                self._max_primary_reached_raw = primary_raw
            if abs(cross) > self._max_abs_cross:
                self._max_abs_cross = abs(cross)
                self._max_abs_cross_raw = abs(cross_raw)

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

    def _feed_idle(self, sample: PositionSample) -> None:
        """진행 중인 방향이 없을 때(IDLE/DIRECTION_DONE) 호출된다 - 원점 부근에서 안정적으로
        멈춰 있던 마지막 위치를 "대기 baseline"으로 계속 갱신해두고, 거기서 한 축으로
        auto_direction_threshold_moa 이상 벗어나면 그 축+부호로 방향을 정해 자동으로 그
        방향 시험을 시작한다. 이때 baseline은 지금 이 샘플이 아니라 "직전에 안정적으로
        멈춰 있던 위치"를 그대로 쓴다(사용자 요청: 버튼 클릭 시점이 아니라 실제로 멈춰 있던
        지점이 더 정확함, 2026-09-16)."""
        state = self._idle_stability.feed(sample)
        if state.is_stable and state.stable_position is not None:
            self._idle_baseline = state.stable_position

        if self._idle_baseline is None:
            return

        bx, by = self._idle_baseline
        dx = sample.x_moa - bx
        dy = sample.y_moa - by
        threshold = self.settings.auto_direction_threshold_moa
        if abs(dx) < threshold and abs(dy) < threshold:
            return

        if abs(dy) >= abs(dx):
            direction = TravelDirection.UP if dy > 0 else TravelDirection.DOWN
        else:
            direction = TravelDirection.RIGHT if dx > 0 else TravelDirection.LEFT

        self.start_direction(direction)  # 큐/planned_directions 관리 로직 재사용

        # start_direction() -> _reset_direction_buffers()가 baseline 캡처 상태를 지웠으므로,
        # 대기 중 추적해둔 baseline 값으로 즉시 다시 채워넣는다 - 아래 feed_position()의
        # "if not self._baseline_captured" 블록이 (현재 이동 중인) 이 샘플을 baseline으로
        # 잘못 잡지 않도록 함.
        self._baseline_x_moa, self._baseline_y_moa = bx, by
        self._baseline_primary, self._baseline_cross = resolve_primary_and_cross(direction, bx, by)
        self._baseline_captured = True

        self._idle_baseline = None
        self._idle_stability.reset()

    # ---- 작업자 액션 ----
    def set_dead_click(self, direction: TravelDirection, flagged: bool) -> None:
        """방향 완료 후 결과표에서 O/X로 토글한다(flag_dead_click()의 즉시-중단 방식을
        대체) - 시험 도중 바로 누르게 하면 흐름이 끊기므로, 네 방향이 다 끝난 뒤 작업자가
        기억을 더듬어 한 번에 토글하는 방식으로 바꿨다(사용자 요청, 2026-09-16). flagged가
        True면 그 방향의 최종 판정을 측정 결과와 무관하게 강제로 FAIL로 덮어쓰고, False면
        데드클릭 항목을 지우고 원래 측정 기반 판정으로 되돌린다. 대상 방향이 아직 한 번도
        완료된 적 없으면(direction_results에 없으면) 예외를 던진다."""
        result = next((r for r in self.direction_results if r.direction == direction), None)
        if result is None:
            raise RuntimeError(f"{direction.value}는 아직 완료된 적이 없어 데드클릭을 표시할 수 없습니다.")

        result.check_results = [c for c in result.check_results if c.check_type != CheckType.DEAD_CLICK]
        if flagged:
            result.check_results.append(
                CheckResult(
                    check_type=CheckType.DEAD_CLICK,
                    measured_value=None,
                    threshold_used=None,
                    status=Verdict.FAIL,
                )
            )
        result.verdict = Verdict.FAIL if any(c.status == Verdict.FAIL for c in result.check_results) else Verdict.PASS

        if self.direction_results:
            self.overall_verdict = (
                Verdict.PASS
                if all(r.verdict == Verdict.PASS for r in self.direction_results)
                else Verdict.FAIL
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
                raw_measured_value=self._max_primary_reached_raw,
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
                raw_measured_value=self._max_abs_cross_raw,
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
                raw_measured_value=abs(self._last_cross_raw),
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
                raw_measured_value=abs(self._last_primary_raw),
            )
        )
        self._finalize_direction(checks, Verdict.PASS if backlash_ok else Verdict.FAIL)

    # ---- 내부 ----
    def _reset_idle_tracking(self) -> None:
        """대기(idle) 안정성 추적을 완전히 새로 시작한다 - 방향이 시작되거나 끝날 때
        (_reset_direction_buffers/_finalize_direction) 호출하지 않으면, 방향이 시작되기
        전에 이미 쌓여 있던 오래된 안정 시작 시각(_stable_since_s)이 그대로 남아있다가,
        방향이 끝나고 우연히 같은(원점 근처) 위치에서 첫 idle 샘플을 받는 순간 "경과 시간"이
        이미 3초를 훌쩍 넘긴 것처럼 계산되어 대기 카운트다운 없이 즉시 "정렬 완료"로
        오판되는 문제가 있었다(실측으로 확인, 2026-09-16: 원점 근처로 가면 바로 완료 메시지가
        뜸 + 메시지 4개가 한꺼번에 뜸)."""
        self._idle_stability = self._stability_factory()
        self._idle_baseline = None

    def _reset_direction_buffers(self) -> None:
        self._stability = self._stability_factory()
        self._reset_idle_tracking()
        self._max_primary_reached = 0.0
        self._max_abs_cross = 0.0
        self._last_primary = 0.0
        self._last_cross = 0.0
        self._max_primary_reached_raw = 0.0
        self._max_abs_cross_raw = 0.0
        self._last_primary_raw = 0.0
        self._last_cross_raw = 0.0
        self._baseline_captured = False
        self._baseline_x_moa = 0.0
        self._baseline_y_moa = 0.0
        self._baseline_primary = 0.0
        self._baseline_cross = 0.0
        self._pending_checks = []

    def _finalize_direction(self, checks: list[CheckResult], verdict: Verdict) -> None:
        direction = self.current_direction
        assert direction is not None

        attempt_number = self._attempt_counters.get(direction, 0) + 1
        self._attempt_counters[direction] = attempt_number

        start_point = (self._baseline_x_moa, self._baseline_y_moa) if self._baseline_captured else None
        result = DirectionTestResult(
            direction=direction,
            attempt_number=attempt_number,
            verdict=verdict,
            check_results=checks,
            start_point_moa=start_point,
        )
        self.direction_results.append(result)

        self.current_direction = None
        self.phase = Phase.DIRECTION_DONE
        # 방향이 막 끝나 다시 대기(idle) 상태로 돌아가는 시점 - idle 추적을 깨끗하게
        # 새로 시작해야 한다(_reset_idle_tracking 참고, 실측 버그 수정 2026-09-16).
        self._reset_idle_tracking()

        if self._sequential_queue_mode:
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
        else:
            # 자유 순서(방향별 박스) 모드: 한 방향의 불량이 다른 방향 시작을 막지 않음 - 계획된
            # 방향이 모두 시도되어야 전체 종료로 간주(그때까지는 어떤 박스든 계속 (재)시작 가능).
            attempted = {r.direction for r in self.direction_results}
            if set(self._planned_directions) <= attempted:
                self.overall_verdict = (
                    Verdict.PASS
                    if all(r.verdict == Verdict.PASS for r in self.direction_results)
                    else Verdict.FAIL
                )
                self.phase = Phase.INSPECTION_DONE
