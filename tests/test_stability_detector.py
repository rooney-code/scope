from core.tracking.position_sample import PositionSample
from core.tracking.stability_detector import StabilityDetector


def _feed_constant(detector, x, y, n, start_t=0.0, dt=0.02):
    result = None
    for i in range(n):
        sample = PositionSample(timestamp_s=start_t + i * dt, x_moa=x, y_moa=y)
        result = detector.feed(sample)
    return result


def test_becomes_stable_after_window_and_duration():
    detector = StabilityDetector(window_size_samples=5, variance_threshold_moa2=0.01, min_stable_duration_ms=50)
    # 5 samples at dt=0.02s => window fills at t=0.08s, then need +50ms more stable
    result = _feed_constant(detector, x=10.0, y=5.0, n=5, dt=0.02)
    assert not result.is_stable  # 윈도우는 찼지만 아직 최소 지속시간 미달

    # 추가 샘플로 지속시간 채우기
    result = None
    for i in range(5, 10):
        sample = PositionSample(timestamp_s=i * 0.02, x_moa=10.0, y_moa=5.0)
        result = detector.feed(sample)
    assert result.is_stable
    assert abs(result.stable_position[0] - 10.0) < 1e-6
    assert abs(result.stable_position[1] - 5.0) < 1e-6


def test_noisy_but_within_threshold_is_stable():
    detector = StabilityDetector(window_size_samples=6, variance_threshold_moa2=0.05, min_stable_duration_ms=10)
    values = [10.0, 10.05, 9.95, 10.02, 9.98, 10.01, 10.0, 9.99, 10.0, 10.0]
    result = None
    for i, v in enumerate(values):
        sample = PositionSample(timestamp_s=i * 0.02, x_moa=v, y_moa=0.0)
        result = detector.feed(sample)
    assert result.is_stable


def test_jump_resets_stability():
    detector = StabilityDetector(window_size_samples=5, variance_threshold_moa2=0.01, min_stable_duration_ms=30)
    # 안정 상태 도달
    result = None
    for i in range(10):
        sample = PositionSample(timestamp_s=i * 0.02, x_moa=10.0, y_moa=0.0)
        result = detector.feed(sample)
    assert result.is_stable

    # 갑작스런 이동(사용자가 조작 재개) -> 불안정으로 전환
    result = detector.feed(PositionSample(timestamp_s=0.30, x_moa=20.0, y_moa=0.0))
    assert not result.is_stable


def test_reset_clears_state():
    detector = StabilityDetector(window_size_samples=3, variance_threshold_moa2=0.01, min_stable_duration_ms=10)
    for i in range(6):
        detector.feed(PositionSample(timestamp_s=i * 0.02, x_moa=1.0, y_moa=1.0))
    detector.reset()
    result = detector.feed(PositionSample(timestamp_s=100.0, x_moa=1.0, y_moa=1.0))
    assert not result.is_stable  # 리셋 후 윈도우가 다시 차야 함
