import pytest

from core.config.settings import Stage2Settings
from core.inspection.models import CheckType, TravelDirection, Verdict
from core.inspection.travel_test_state_machine import Phase, TravelTestStateMachine
from core.tracking.position_sample import PositionSample
from core.tracking.stability_detector import StabilityDetector


def _fast_stability_factory():
    return StabilityDetector(window_size_samples=3, variance_threshold_moa2=0.5, min_stable_duration_ms=10)


def _make_machine(**overrides):
    settings = Stage2Settings(
        travel_target_moa=35.0,
        drift_threshold_moa=2.5,
        shift_threshold_moa=2.5,
        backlash_threshold_moa=2.5,
        near_zero_band_moa=5.0,
        stop_on_failure_scope="entire_inspection",
    )
    for k, v in overrides.items():
        setattr(settings, k, v)
    return TravelTestStateMachine(settings, stability_detector_factory=_fast_stability_factory)


def _feed_ramp(machine, x_values, y_values, t0=0.0, dt=0.02):
    t = t0
    for x, y in zip(x_values, y_values):
        machine.feed_position(PositionSample(timestamp_s=t, x_moa=x, y_moa=y))
        t += dt
    return t


def _feed_hold(machine, x, y, n, t0, dt=0.02):
    t = t0
    for _ in range(n):
        machine.feed_position(PositionSample(timestamp_s=t, x_moa=x, y_moa=y))
        t += dt
    return t


def test_up_direction_full_pass_auto_detected_without_buttons():
    """'이동 완료'/'원점 복귀 완료' 버튼 없이, 목표/원점 근처에서 잠깐 멈추는 것만으로
    자동 평가되어야 한다 (실제 작업자가 하던 방식 그대로: 이동 -> 눈금 확인(멈춤) -> 후진)."""
    m = _make_machine()
    m.configure([TravelDirection.UP])
    assert m.start_next_direction() == TravelDirection.UP
    assert m.phase == Phase.OUTBOUND

    # 0 -> 35 MOA (y축), cross(x)는 0 유지, 목표 근처에서 멈춤 -> 자동으로 이동량/쉬프트/드리프트 평가
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[i * 5 for i in range(8)])  # 0,5,...,35
    t = _feed_hold(m, x=0, y=35, n=6, t0=t)
    assert m.phase == Phase.RETURN  # 버튼 없이 자동 전환됨

    # 복귀: 35 -> 0, 원점 근처에서 멈춤 -> 자동으로 백래쉬 평가 + 방향 종료
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[35 - i * 5 for i in range(8)])
    _feed_hold(m, x=0, y=0, n=6, t0=t)

    assert m.phase == Phase.INSPECTION_DONE
    assert m.overall_verdict == Verdict.PASS
    result = m.direction_results[-1]
    assert result.verdict == Verdict.PASS
    types = [c.check_type for c in result.check_results]
    assert types == [CheckType.TRAVEL_AMOUNT, CheckType.SHIFT, CheckType.DRIFT, CheckType.BACKLASH]
    assert all(c.status == Verdict.PASS for c in result.check_results)


def test_pausing_mid_route_does_not_trigger_evaluation():
    """목표(35MOA)에 도달하기 전에 잠깐 멈추는 것(예: 손 고쳐잡기)은 평가 트리거로 치지 않고
    무시해야 한다 - 그렇지 않으면 아직 이동 중인데 이동량 미달로 오판된다."""
    m = _make_machine()
    m.configure([TravelDirection.UP])
    m.start_next_direction()

    t = _feed_ramp(m, x_values=[0] * 4, y_values=[0, 5, 10, 15])
    t = _feed_hold(m, x=0, y=15, n=6, t0=t)  # 15MOA에서 멈춤 - 아직 목표(35) 못 미침

    assert m.phase == Phase.OUTBOUND  # 자동 평가 트리거 안 됨, 계속 진행 중
    assert m.direction_results == []

    # 계속 이동해서 목표까지 도달하면 그제서야 평가됨
    t = _feed_ramp(m, x_values=[0] * 4, y_values=[20, 25, 30, 35], t0=t)
    _feed_hold(m, x=0, y=35, n=6, t0=t)
    assert m.phase == Phase.RETURN


def test_travel_amount_shortfall_requires_manual_confirmation():
    """기계적 한계로 목표(35)에 못 미치는 경우, 그 지점에서 멈춰도 자동으로는 평가되지 않는다
    (목표 근처가 아니므로) - 작업자가 "여기까지가 한계"라고 mark_far_point_reached()를 수동
    호출해야 한다."""
    m = _make_machine()
    m.configure([TravelDirection.UP])
    m.start_next_direction()

    t = _feed_ramp(m, x_values=[0] * 6, y_values=[0, 5, 10, 15, 20, 25])  # 목표(35) 미달
    _feed_hold(m, x=0, y=25, n=6, t0=t)
    assert m.phase == Phase.OUTBOUND  # 자동 트리거 안 됨

    m.mark_far_point_reached()  # 수동 확정

    assert m.phase == Phase.INSPECTION_DONE
    result = m.direction_results[-1]
    assert result.verdict == Verdict.FAIL
    assert result.check_results[0].check_type == CheckType.TRAVEL_AMOUNT
    assert result.check_results[0].status == Verdict.FAIL
    # 이동량이 불합격이어도 측정 가능한 쉬프트/드리프트는 계속 기록한다(백래쉬만 원점 복귀가
    # 필요해서 기록하지 않음) - 사용자 요청, 2026-09-16: "하나의 불량이 발생하면 나머지
    # 항목은 기록하지 않는" 문제 개선. 원점 복귀를 강제로 요구하지 않는 절충안이라 백래쉬
    # 항목만 없다.
    assert {c.check_type for c in result.check_results} == {
        CheckType.TRAVEL_AMOUNT,
        CheckType.SHIFT,
        CheckType.DRIFT,
    }


def test_shift_exceeds_threshold_fails_automatically():
    m = _make_machine()
    m.configure([TravelDirection.UP])
    m.start_next_direction()

    # 이동 경로 중간에 교차축이 크게 흔들림(4 moa > 2.5 임계값)
    xs = [0, 1, 4.0, 1, 0, 0, 0, 0]
    ys = [0, 5, 10, 15, 20, 25, 30, 35]
    t = _feed_ramp(m, x_values=xs, y_values=ys)
    _feed_hold(m, x=0, y=35, n=6, t0=t)  # 목표 근처에서 멈춤 -> 자동 평가되어 즉시 불량 종료

    assert m.phase == Phase.INSPECTION_DONE
    result = m.direction_results[-1]
    assert result.verdict == Verdict.FAIL
    # 쉬프트가 불합격이어도 (순서상 뒤에 계산되는) 드리프트까지 계속 기록되므로, 이제
    # check_results의 마지막 항목이 꼭 실패한 항목이라는 보장이 없다 - 타입으로 찾는다.
    shift_check = next(c for c in result.check_results if c.check_type == CheckType.SHIFT)
    assert shift_check.status == Verdict.FAIL


def test_drift_exceeds_threshold_fails_automatically():
    # shift 임계값을 넉넉히 잡아 shift는 통과하고 drift만 걸리도록 함
    m = _make_machine(shift_threshold_moa=5.0)
    m.configure([TravelDirection.UP])
    m.start_next_direction()

    t = _feed_ramp(m, x_values=[0] * 8, y_values=[i * 5 for i in range(8)])
    # 목표 지점에서 교차축이 3.0 moa로 안정 (임계값 2.5 초과) -> 멈추는 순간 자동 평가
    _feed_hold(m, x=3.0, y=35, n=6, t0=t)

    assert m.phase == Phase.INSPECTION_DONE
    result = m.direction_results[-1]
    assert result.verdict == Verdict.FAIL
    assert result.check_results[-1].check_type == CheckType.DRIFT
    assert result.check_results[-1].status == Verdict.FAIL


def test_backlash_exceeds_threshold_fails_automatically():
    """백래쉬 자체가 '원점 근처인데 0은 아닌' 잔류오차를 재는 값이므로, 원점 복귀 멈춤의
    자동 감지 범위(near_zero_band_moa)는 이 실패 케이스도 덮을 만큼 넉넉해야 한다."""
    m = _make_machine()
    m.configure([TravelDirection.UP])
    m.start_next_direction()

    t = _feed_ramp(m, x_values=[0] * 8, y_values=[i * 5 for i in range(8)])
    t = _feed_hold(m, x=0, y=35, n=6, t0=t)
    assert m.phase == Phase.RETURN

    # 복귀했지만 3.0 moa 만큼 잔류오차 (임계값 2.5 초과, near_zero_band_moa=5.0 안쪽이라 자동 감지됨)
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[35 - i * 4 for i in range(8)])
    _feed_hold(m, x=0, y=3.0, n=6, t0=t)

    assert m.phase == Phase.INSPECTION_DONE
    result = m.direction_results[-1]
    assert result.verdict == Verdict.FAIL
    assert result.check_results[-1].check_type == CheckType.BACKLASH
    assert result.check_results[-1].status == Verdict.FAIL


def test_measurements_are_corrected_relative_to_actual_start_point_not_absolute_origin():
    """시험 시작점이 그리드 절대 원점(0,0)과 정확히 일치할 가능성은 낮다(사용자 확인 사항,
    2026-09-13) - 예: 실제로는 (0.1, 0.1)에서 시작. 이 경우 판정에 쓰이는 measured_value는
    항상 시작점 기준 상대값이어야 하고(절대 원점 기준 raw_measured_value는 참고용으로 별도
    보존), start_point_moa에 실제 시작 좌표가 기록되어야 한다."""
    m = _make_machine()
    m.configure([TravelDirection.UP])
    m.start_next_direction()

    # 시작점이 (0.1, 0.1) - 그리드 절대 원점이 아님
    t = _feed_ramp(m, x_values=[0.1] * 4, y_values=[0.1, 10, 20, 30])
    # 목표(35) 지점에서 cross(x)가 절대 원점 기준 2.4moa로 안정 - 시작점(0.1) 기준으로는 2.3moa
    t = _feed_hold(m, x=2.4, y=35.1, n=6, t0=t)
    assert m.phase == Phase.RETURN

    # 복귀도 시작점(0.1, 0.1) 기준으로 완료
    t = _feed_ramp(m, x_values=[0.1] * 4, y_values=[20, 10, 5, 0.1], t0=t)
    _feed_hold(m, x=0.1, y=0.1, n=6, t0=t)

    assert m.phase == Phase.INSPECTION_DONE
    result = m.direction_results[-1]
    assert result.start_point_moa == pytest.approx((0.1, 0.1))

    shift = next(c for c in result.check_results if c.check_type == CheckType.SHIFT)
    assert shift.measured_value == pytest.approx(2.3, abs=1e-9)  # 시작점 보정된 최종값(판정에 사용)
    assert shift.raw_measured_value == pytest.approx(2.4, abs=1e-9)  # 절대 원점 기준 원시값(참고용)
    assert shift.status == Verdict.PASS  # 2.3 <= 2.5 임계값

    travel = next(c for c in result.check_results if c.check_type == CheckType.TRAVEL_AMOUNT)
    assert travel.measured_value == pytest.approx(35.0, abs=1e-9)  # 35.1 - 0.1(시작점 보정)
    assert travel.raw_measured_value == pytest.approx(35.1, abs=1e-9)


def test_set_dead_click_overrides_verdict_after_direction_completes():
    """시험 중 바로 누르면 흐름이 끊기므로, 방향이 끝난 뒤 결과표에서 O/X로 토글하는 방식
    (사용자 요청, 2026-09-16) - 켜면(True) 측정 결과와 무관하게 FAIL로 덮어쓰고, 끄면(False)
    원래 측정 기반 판정으로 되돌아가야 한다."""
    m = _make_machine()
    m.configure([TravelDirection.UP])
    m.start_next_direction()
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[i * 5 for i in range(8)])
    t = _feed_hold(m, x=0, y=35, n=6, t0=t)
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[35 - i * 5 for i in range(8)], t0=t)
    _feed_hold(m, x=0, y=0, n=6, t0=t)

    result = m.direction_results[-1]
    assert result.verdict == Verdict.PASS  # 측정만으로는 합격
    assert m.overall_verdict == Verdict.PASS

    m.set_dead_click(TravelDirection.UP, True)
    result = m.direction_results[-1]
    assert result.verdict == Verdict.FAIL
    assert any(c.check_type == CheckType.DEAD_CLICK and c.status == Verdict.FAIL for c in result.check_results)
    assert m.overall_verdict == Verdict.FAIL

    m.set_dead_click(TravelDirection.UP, False)
    result = m.direction_results[-1]
    assert result.verdict == Verdict.PASS  # 원래 측정 기반 판정으로 복원
    assert not any(c.check_type == CheckType.DEAD_CLICK for c in result.check_results)
    assert m.overall_verdict == Verdict.PASS


def test_set_dead_click_raises_for_direction_never_completed():
    m = _make_machine()
    with pytest.raises(RuntimeError):
        m.set_dead_click(TravelDirection.UP, True)


def test_auto_detects_up_direction_from_idle_movement():
    """버튼 없이도, 원점 부근에서 안정적으로 대기하던 위치에서 한 축으로
    auto_direction_threshold_moa 이상 벗어나면 자동으로 그 방향 시험이 시작돼야 한다
    (사용자 요청, 2026-09-16 - 매 방향마다 버튼을 누르는 번거로움을 줄이기 위함). baseline은
    이동 중인 지금 이 샘플이 아니라 직전에 안정적으로 멈춰 있던 위치를 그대로 써야 한다."""
    m = _make_machine()
    assert m.phase == Phase.IDLE
    assert m.current_direction is None

    t = _feed_hold(m, x=0.0, y=0.0, n=5, t0=0.0)  # 원점에서 안정적으로 대기(idle baseline 확정)
    m.feed_position(PositionSample(timestamp_s=t, x_moa=0.0, y_moa=4.0))  # 3MOA 이상 위로 이동

    assert m.phase == Phase.OUTBOUND
    assert m.current_direction == TravelDirection.UP
    assert m.baseline_moa == pytest.approx((0.0, 0.0))


def test_auto_detects_right_direction_with_correct_sign():
    m = _make_machine()
    t = _feed_hold(m, x=0.0, y=0.0, n=5, t0=0.0)
    m.feed_position(PositionSample(timestamp_s=t, x_moa=4.0, y_moa=0.0))

    assert m.current_direction == TravelDirection.RIGHT


def test_movement_below_auto_threshold_stays_idle():
    m = _make_machine()
    t = _feed_hold(m, x=0.0, y=0.0, n=5, t0=0.0)
    m.feed_position(PositionSample(timestamp_s=t, x_moa=0.0, y_moa=1.0))  # 3MOA 미만

    assert m.phase == Phase.IDLE
    assert m.current_direction is None


def test_idle_stable_position_far_from_origin_is_not_adopted_as_baseline():
    """대기 baseline은 "원점 부근"에서 안정된 경우에만 채택해야 한다 - 방향 시험이 목표
    도달 전(예: 쉬프트 초과)에 조기 종료돼 레드닷이 원점이 아닌 곳(예: 35MOA 부근)에 멈춰
    있으면, 그 자리가 그대로 baseline이 돼버려 (a) "원점 정렬 완료"로 잘못 안내되고
    (b) 거기서 조금만 더 움직여도 엉뚱한 지점을 기준으로 새 방향 시험이 자동 시작되는 버그가
    실측으로 확인됐다(2026-09-16). near_zero_band_moa(5.0) 밖인 y=35에서 안정돼도
    idle_baseline_captured가 False로 남고, 거기서 더 움직여도 자동으로 방향이 시작되면
    안 된다."""
    m = _make_machine()
    t = _feed_hold(m, x=0.0, y=35.0, n=5, t0=0.0)  # 원점이 아닌 곳(목표 근처)에서 안정적으로 대기
    assert not m.idle_baseline_captured

    m.feed_position(PositionSample(timestamp_s=t, x_moa=0.0, y_moa=39.0))  # 그 자리에서 4MOA 더 이동

    assert m.phase == Phase.IDLE
    assert m.current_direction is None


def test_abort_discards_without_recording_and_allows_restart():
    m = _make_machine()
    m.configure([TravelDirection.UP])
    m.start_next_direction()

    _feed_ramp(m, x_values=[0, 0], y_values=[0, 10])
    m.abort_current_direction()

    assert m.direction_results == []  # 폐기됨, 기록 없음
    assert m.phase == Phase.DIRECTION_DONE
    assert TravelDirection.UP in m.direction_queue  # 재시작 가능하도록 큐에 복귀

    # 재시작 - 목표/원점 근처에서 멈추면 자동으로 평가/종료됨
    assert m.start_next_direction() == TravelDirection.UP
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[i * 5 for i in range(8)])
    t = _feed_hold(m, x=0, y=35, n=6, t0=t)
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[35 - i * 5 for i in range(8)])
    _feed_hold(m, x=0, y=0, n=6, t0=t)

    assert len(m.direction_results) == 1
    assert m.direction_results[0].verdict == Verdict.PASS
    assert m.direction_results[0].attempt_number == 1  # abort된 시도는 카운트되지 않음


def test_direction_only_scope_continues_to_next_direction_after_failure():
    m = _make_machine(stop_on_failure_scope="direction_only")
    m.configure([TravelDirection.UP, TravelDirection.DOWN])
    m.start_next_direction()

    # UP 방향 이동량 부족(목표 미달, 자동 감지 안 됨) -> 수동으로 확정 -> 불량이지만
    # direction_only이므로 검사 계속
    t = _feed_ramp(m, x_values=[0] * 4, y_values=[0, 5, 10, 15])
    _feed_hold(m, x=0, y=15, n=6, t0=t)
    m.mark_far_point_reached()

    assert m.phase == Phase.DIRECTION_DONE
    assert m.overall_verdict == Verdict.IN_PROGRESS  # 아직 전체 종료 아님
    assert m.has_next_direction()

    # DOWN 방향 진행 (주축 = -y_moa), 목표/원점 근처에서 멈추면 자동으로 평가/종료
    assert m.start_next_direction() == TravelDirection.DOWN
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[-i * 5 for i in range(8)])
    t = _feed_hold(m, x=0, y=-35, n=6, t0=t)
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[-35 + i * 5 for i in range(8)])
    _feed_hold(m, x=0, y=0, n=6, t0=t)

    assert m.phase == Phase.INSPECTION_DONE
    assert m.overall_verdict == Verdict.FAIL  # UP이 불량이었으므로 전체는 불량
    assert len(m.direction_results) == 2


def test_retest_direction_discards_previous_record_and_resumes_remaining_queue():
    """상 방향이 (조작 실수로) 불량 처리된 뒤 재시험하면: 이전 기록은 즉시 사라지고, 아직
    시도하지 못한 나머지 방향(하)도 큐에 복원되어 이어서 진행된다."""
    m = _make_machine()  # stop_on_failure_scope="entire_inspection" (기본값)
    m.configure([TravelDirection.UP, TravelDirection.DOWN])
    m.start_next_direction()

    # UP: 이동량 미달로 불량 -> entire_inspection이라 전체 검사 종료(하는 시도조차 못 함)
    t = _feed_ramp(m, x_values=[0] * 4, y_values=[0, 5, 10, 15])
    _feed_hold(m, x=0, y=15, n=6, t0=t)
    m.mark_far_point_reached()

    assert m.phase == Phase.INSPECTION_DONE
    assert m.overall_verdict == Verdict.FAIL
    assert len(m.direction_results) == 1
    assert m.direction_results[0].direction == TravelDirection.UP
    assert m.direction_results[0].verdict == Verdict.FAIL

    # 재시험: 이전 UP 기록은 사라지고, UP + (아직 못한) DOWN이 큐에 복원됨
    m.retest_direction(TravelDirection.UP)
    assert m.direction_results == []  # 즉시 초기화됨(이력 보존 없음)
    assert list(m.direction_queue) == [TravelDirection.UP, TravelDirection.DOWN]
    assert m.overall_verdict == Verdict.IN_PROGRESS

    # UP 재시험: 이번엔 정상적으로 합격
    assert m.start_next_direction() == TravelDirection.UP
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[i * 5 for i in range(8)])
    t = _feed_hold(m, x=0, y=35, n=6, t0=t)
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[35 - i * 5 for i in range(8)])
    _feed_hold(m, x=0, y=0, n=6, t0=t)
    assert m.direction_results[-1].verdict == Verdict.PASS
    assert m.has_next_direction()  # DOWN이 이어서 대기 중

    # DOWN도 정상 진행
    assert m.start_next_direction() == TravelDirection.DOWN
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[-i * 5 for i in range(8)])
    t = _feed_hold(m, x=0, y=-35, n=6, t0=t)
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[-35 + i * 5 for i in range(8)])
    _feed_hold(m, x=0, y=0, n=6, t0=t)

    assert m.phase == Phase.INSPECTION_DONE
    assert m.overall_verdict == Verdict.PASS  # 옛 UP 불량 기록이 남아있지 않으므로 전체 합격
    assert len(m.direction_results) == 2


def test_retest_direction_requires_completed_direction():
    m = _make_machine()
    m.configure([TravelDirection.UP])
    with pytest.raises(RuntimeError):
        m.retest_direction(TravelDirection.UP)  # 아직 완료된 적 없음


def test_finalize_locks_out_further_retest():
    m = _make_machine()
    m.configure([TravelDirection.UP])
    m.start_next_direction()
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[i * 5 for i in range(8)])
    t = _feed_hold(m, x=0, y=35, n=6, t0=t)
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[35 - i * 5 for i in range(8)])
    _feed_hold(m, x=0, y=0, n=6, t0=t)

    assert m.is_ready_to_finalize
    m.finalize()
    assert m.finalized
    assert not m.is_ready_to_finalize

    with pytest.raises(RuntimeError):
        m.retest_direction(TravelDirection.UP)


def test_start_direction_lets_any_direction_go_in_any_order():
    """start_direction()은 순서 큐 없이 어떤 방향이든 바로 시작할 수 있다 - configure() 없이도
    동작(기본 계획 방향 = 4방향 전체)."""
    m = _make_machine()
    assert m.start_direction(TravelDirection.RIGHT) == TravelDirection.RIGHT
    assert m.phase == Phase.OUTBOUND
    assert m.current_direction == TravelDirection.RIGHT


def test_starting_different_direction_discards_in_progress_attempt_without_recording():
    """좌 시험 진행 중 우 시작을 누르면: 좌의 미완성 진행 데이터는 그냥 버려지고(기록 없음),
    우가 새로 시작된다 - 동시 진행 불가 + 재시작 전 항상 원점 복귀가 필요하므로 부분 기록은
    의미가 없다는 사용자 요구사항."""
    m = _make_machine()
    m.start_direction(TravelDirection.LEFT)
    _feed_ramp(m, x_values=[-5, -10, -15], y_values=[0, 0, 0])  # 좌 진행 중(미완성)

    m.start_direction(TravelDirection.RIGHT)  # 좌를 중간에 버리고 우로 전환

    assert m.current_direction == TravelDirection.RIGHT
    assert m.phase == Phase.OUTBOUND
    assert m.direction_results == []  # 좌의 미완성 데이터는 기록되지 않음


def test_free_order_mode_one_failure_does_not_block_other_directions():
    """자유 순서 모드에서는 한 방향이 불량이어도 다른 방향을 계속 시작할 수 있고, 계획된
    4방향이 모두 시도되어야 전체 종료로 간주된다."""
    m = _make_machine()  # stop_on_failure_scope="entire_inspection" (기본값)이어도 무시됨
    m.start_direction(TravelDirection.UP)

    # UP: 이동량 미달로 불량
    t = _feed_ramp(m, x_values=[0] * 4, y_values=[0, 5, 10, 15])
    _feed_hold(m, x=0, y=15, n=6, t0=t)
    m.mark_far_point_reached()

    assert m.direction_results[-1].verdict == Verdict.FAIL
    assert m.phase == Phase.DIRECTION_DONE  # 전체 종료로 안 넘어감(자유 순서 모드)
    # 불량이 나거나 일부만 하고 끝내고 싶을 때도 종료할 수 있어야 한다는 요청(2026-09-16)에
    # 따라, 완료된 방향이 하나라도 있고 지금 진행 중이 아니면 이미 종료 가능해야 한다 -
    # "4방향 전부 시도"를 기다릴 필요 없음.
    assert m.is_ready_to_finalize

    # UP이 불량이었어도 DOWN/LEFT/RIGHT를 자유롭게 시작 가능
    for direction, y_sign in [(TravelDirection.DOWN, -1), (TravelDirection.LEFT, -1), (TravelDirection.RIGHT, 1)]:
        assert m.start_direction(direction) == direction
        if direction in (TravelDirection.DOWN, TravelDirection.UP):
            t = _feed_ramp(m, x_values=[0] * 8, y_values=[y_sign * i * 5 for i in range(8)])
            t = _feed_hold(m, x=0, y=y_sign * 35, n=6, t0=t)
            t = _feed_ramp(m, x_values=[0] * 8, y_values=[y_sign * (35 - i * 5) for i in range(8)])
            _feed_hold(m, x=0, y=0, n=6, t0=t)
        else:
            t = _feed_ramp(m, x_values=[y_sign * i * 5 for i in range(8)], y_values=[0] * 8)
            t = _feed_hold(m, x=y_sign * 35, y=0, n=6, t0=t)
            t = _feed_ramp(m, x_values=[y_sign * (35 - i * 5) for i in range(8)], y_values=[0] * 8)
            _feed_hold(m, x=0, y=0, n=6, t0=t)

    assert m.phase == Phase.INSPECTION_DONE
    assert m.is_ready_to_finalize
    assert m.overall_verdict == Verdict.FAIL  # UP 불량이 남아있으므로 전체는 불량
    assert len(m.direction_results) == 4


def test_start_direction_on_completed_direction_discards_old_record_like_retest():
    m = _make_machine()
    m.start_direction(TravelDirection.UP)
    t = _feed_ramp(m, x_values=[0] * 4, y_values=[0, 5, 10, 15])
    _feed_hold(m, x=0, y=15, n=6, t0=t)
    m.mark_far_point_reached()
    assert m.direction_results[-1].verdict == Verdict.FAIL

    m.start_direction(TravelDirection.UP)  # 같은 박스를 다시 누름 = 재시작
    assert m.direction_results == []  # 이전 불량 기록은 즉시 사라짐
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[i * 5 for i in range(8)])
    t = _feed_hold(m, x=0, y=35, n=6, t0=t)
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[35 - i * 5 for i in range(8)])
    _feed_hold(m, x=0, y=0, n=6, t0=t)
    assert m.direction_results[-1].verdict == Verdict.PASS


def test_finalize_before_all_directions_done_raises():
    m = _make_machine()
    m.configure([TravelDirection.UP, TravelDirection.DOWN])
    m.start_next_direction()
    assert not m.is_ready_to_finalize
    with pytest.raises(RuntimeError):
        m.finalize()


def test_can_finalize_after_just_one_direction_when_nothing_in_progress():
    """불량이 나거나 사용자가 일부 방향만 하고 끝내고 싶을 때도 종료할 수 있어야 한다는
    요청(2026-09-16) - 4방향을 다 시도하지 않아도, 지금 진행 중인 방향이 없고 완료된
    방향이 하나 이상이면 finalize()가 가능해야 하고, overall_verdict도 그 시점에 정확히
    계산돼야 한다."""
    m = _make_machine()
    m.start_direction(TravelDirection.UP)
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[i * 5 for i in range(8)])
    t = _feed_hold(m, x=0, y=35, n=6, t0=t)
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[35 - i * 5 for i in range(8)])
    _feed_hold(m, x=0, y=0, n=6, t0=t)

    assert m.phase == Phase.DIRECTION_DONE  # UP만 끝났고 나머지 3방향은 안 함
    assert m.is_ready_to_finalize

    m.finalize()

    assert m.finalized
    assert m.overall_verdict == Verdict.PASS  # 시도한 방향(UP)이 전부 합격
