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
        near_zero_band_moa=1.0,
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


def test_up_direction_full_pass():
    m = _make_machine()
    m.configure([TravelDirection.UP])
    assert m.start_next_direction() == TravelDirection.UP
    assert m.phase == Phase.OUTBOUND

    # 0 -> 35 MOA (y축), cross(x)는 0 유지
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[i * 5 for i in range(8)])  # 0,5,...,35
    t = _feed_hold(m, x=0, y=35, n=6, t0=t)  # 안정화 -> 드리프트 캡처

    m.mark_far_point_reached()
    assert m.phase == Phase.RETURN

    # 복귀: 35 -> 0
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[35 - i * 5 for i in range(8)])
    t = _feed_hold(m, x=0, y=0, n=6, t0=t)

    m.mark_returned_to_origin()

    assert m.phase == Phase.INSPECTION_DONE
    assert m.overall_verdict == Verdict.PASS
    result = m.direction_results[-1]
    assert result.verdict == Verdict.PASS
    types = [c.check_type for c in result.check_results]
    assert types == [CheckType.TRAVEL_AMOUNT, CheckType.SHIFT, CheckType.DRIFT, CheckType.BACKLASH]
    assert all(c.status == Verdict.PASS for c in result.check_results)


def test_travel_amount_shortfall_fails_immediately():
    m = _make_machine()
    m.configure([TravelDirection.UP])
    m.start_next_direction()

    t = _feed_ramp(m, x_values=[0] * 6, y_values=[0, 5, 10, 15, 20, 25])  # 목표(35) 미달
    _feed_hold(m, x=0, y=25, n=6, t0=t)

    m.mark_far_point_reached()

    assert m.phase == Phase.INSPECTION_DONE
    result = m.direction_results[-1]
    assert result.verdict == Verdict.FAIL
    assert result.check_results[0].check_type == CheckType.TRAVEL_AMOUNT
    assert result.check_results[0].status == Verdict.FAIL
    assert len(result.check_results) == 1  # 이동량에서 즉시 종료, 나머지 체크 없음


def test_shift_exceeds_threshold_fails():
    m = _make_machine()
    m.configure([TravelDirection.UP])
    m.start_next_direction()

    # 이동 경로 중간에 교차축이 크게 흔들림(4 moa > 2.5 임계값)
    xs = [0, 1, 4.0, 1, 0, 0, 0, 0]
    ys = [0, 5, 10, 15, 20, 25, 30, 35]
    t = _feed_ramp(m, x_values=xs, y_values=ys)
    _feed_hold(m, x=0, y=35, n=6, t0=t)

    m.mark_far_point_reached()

    result = m.direction_results[-1]
    assert result.verdict == Verdict.FAIL
    assert result.check_results[-1].check_type == CheckType.SHIFT
    assert result.check_results[-1].status == Verdict.FAIL


def test_drift_exceeds_threshold_fails():
    # shift 임계값을 넉넉히 잡아 shift는 통과하고 drift만 걸리도록 함
    # (경로상 최댓값과 도달 시점 값이 같은 시나리오이므로 두 임계값이 같으면 항상 shift가 먼저 걸림)
    m = _make_machine(shift_threshold_moa=5.0)
    m.configure([TravelDirection.UP])
    m.start_next_direction()

    t = _feed_ramp(m, x_values=[0] * 8, y_values=[i * 5 for i in range(8)])
    # 목표 지점에서 교차축이 3.0 moa로 안정 (임계값 2.5 초과)
    _feed_hold(m, x=3.0, y=35, n=6, t0=t)

    m.mark_far_point_reached()

    result = m.direction_results[-1]
    assert result.verdict == Verdict.FAIL
    assert result.check_results[-1].check_type == CheckType.DRIFT
    assert result.check_results[-1].status == Verdict.FAIL


def test_backlash_exceeds_threshold_fails():
    m = _make_machine()
    m.configure([TravelDirection.UP])
    m.start_next_direction()

    t = _feed_ramp(m, x_values=[0] * 8, y_values=[i * 5 for i in range(8)])
    t = _feed_hold(m, x=0, y=35, n=6, t0=t)
    m.mark_far_point_reached()
    assert m.phase == Phase.RETURN

    # 복귀했지만 3.0 moa 만큼 잔류오차 (임계값 2.5 초과)
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[35 - i * 4 for i in range(8)])
    _feed_hold(m, x=0, y=3.0, n=6, t0=t)
    m.mark_returned_to_origin()

    result = m.direction_results[-1]
    assert result.verdict == Verdict.FAIL
    assert result.check_results[-1].check_type == CheckType.BACKLASH
    assert result.check_results[-1].status == Verdict.FAIL


def test_dead_click_manual_flag_fails_immediately():
    m = _make_machine()
    m.configure([TravelDirection.UP])
    m.start_next_direction()

    _feed_ramp(m, x_values=[0, 0, 0], y_values=[0, 5, 10])
    m.flag_dead_click()

    result = m.direction_results[-1]
    assert result.verdict == Verdict.FAIL
    assert result.check_results[0].check_type == CheckType.DEAD_CLICK


def test_abort_discards_without_recording_and_allows_restart():
    m = _make_machine()
    m.configure([TravelDirection.UP])
    m.start_next_direction()

    _feed_ramp(m, x_values=[0, 0], y_values=[0, 10])
    m.abort_current_direction()

    assert m.direction_results == []  # 폐기됨, 기록 없음
    assert m.phase == Phase.DIRECTION_DONE
    assert TravelDirection.UP in m.direction_queue  # 재시작 가능하도록 큐에 복귀

    # 재시작
    assert m.start_next_direction() == TravelDirection.UP
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[i * 5 for i in range(8)])
    t = _feed_hold(m, x=0, y=35, n=6, t0=t)
    m.mark_far_point_reached()
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[35 - i * 5 for i in range(8)])
    _feed_hold(m, x=0, y=0, n=6, t0=t)
    m.mark_returned_to_origin()

    assert len(m.direction_results) == 1
    assert m.direction_results[0].verdict == Verdict.PASS
    assert m.direction_results[0].attempt_number == 1  # abort된 시도는 카운트되지 않음


def test_direction_only_scope_continues_to_next_direction_after_failure():
    m = _make_machine(stop_on_failure_scope="direction_only")
    m.configure([TravelDirection.UP, TravelDirection.DOWN])
    m.start_next_direction()

    # UP 방향 이동량 부족 -> 불량이지만 direction_only이므로 검사 계속
    _feed_ramp(m, x_values=[0] * 4, y_values=[0, 5, 10, 15])
    m.mark_far_point_reached()

    assert m.phase == Phase.DIRECTION_DONE
    assert m.overall_verdict == Verdict.IN_PROGRESS  # 아직 전체 종료 아님
    assert m.has_next_direction()

    # DOWN 방향 진행 (주축 = -y_moa)
    assert m.start_next_direction() == TravelDirection.DOWN
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[-i * 5 for i in range(8)])
    t = _feed_hold(m, x=0, y=-35, n=6, t0=t)
    m.mark_far_point_reached()
    t = _feed_ramp(m, x_values=[0] * 8, y_values=[-35 + i * 5 for i in range(8)])
    _feed_hold(m, x=0, y=0, n=6, t0=t)
    m.mark_returned_to_origin()

    assert m.phase == Phase.INSPECTION_DONE
    assert m.overall_verdict == Verdict.FAIL  # UP이 불량이었으므로 전체는 불량
    assert len(m.direction_results) == 2
