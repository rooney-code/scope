import math

import numpy as np
import cv2

from core.vision.red_dot_detector import RedDotDetector, DetectionResult
from core.vision.blob_tracker import BlobTracker
from core.config.settings import DetectionSettings


def _make_frame(width=400, height=300, dot_center=(200, 150), dot_size=(10, 10), color_bgr=(40, 160, 230)):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[..., 1] = 60  # 초록 배경
    cv2.ellipse(frame, dot_center, dot_size, 0, 0, 360, color_bgr, -1)
    return frame


def test_detects_circular_dot_near_center():
    frame = _make_frame(dot_center=(200, 150), dot_size=(8, 8))
    detector = RedDotDetector(DetectionSettings())
    result = detector.detect_best(frame)
    assert result.found
    cx, cy = result.center_px
    assert abs(cx - 200) < 2
    assert abs(cy - 150) < 2


def test_detects_elliptical_dot_at_edge():
    frame = _make_frame(dot_center=(350, 50), dot_size=(12, 6))  # 타원형 왜곡 시뮬레이션
    detector = RedDotDetector(DetectionSettings())
    result = detector.detect_best(frame)
    assert result.found
    assert result.ellipse is not None
    cx, cy = result.center_px
    assert abs(cx - 350) < 2
    assert abs(cy - 50) < 2


def _hsv_to_bgr(h: int, s: int, v: int) -> tuple[int, int, int]:
    bgr = cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2BGR)[0][0]
    return tuple(int(c) for c in bgr)


def _make_comet_tail_frame(
    width=400, height=300, head_center=(200, 150), head_radius=8, tail_dx=1, tail_len=30, n_segments=8
):
    """35MOA 근처에서 실측된 "코멧테일"(밝은 머리 + 중심 반대쪽으로 흐려지는 꼬리) 형태를
    흉내낸 합성 이미지 - 머리는 밝고(V=230) 꼬리는 점점 어두워짐(V가 130까지 내려감,
    HSV 임계값 하한(120)보다는 위라 여전히 같은 마스크에 포함됨)."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[..., 1] = 60
    cv2.circle(frame, head_center, head_radius, _hsv_to_bgr(20, 200, 230), -1)
    hx, hy = head_center
    for i in range(1, n_segments + 1):
        frac = i / n_segments
        v = max(int(230 - frac * 100), 120)
        px = int(hx + tail_dx * frac * tail_len)
        r = max(1, int(6 * (1 - frac * 0.7)))
        cv2.circle(frame, (px, hy), r, _hsv_to_bgr(20, 200, v), -1)
    return frame


def test_head_square_center_is_pulled_toward_bright_head_not_binary_centroid():
    """35MOA 부근에서 실측된 "코멧테일" 왜곡(고객 제공 영상으로 확인) - 이진 마스크의 단순
    무게중심(모든 픽셀 동일 가중치)은 어두운 꼬리 쪽으로 쏠린다. 정사각형 분할 방식
    (_fit_head_square, 14차 수정 이후 center_px의 주된 계산 방식)이 그 쏠림을 뚜렷이 줄여
    참 중심(밝은 머리)에 더 가까워야 한다."""
    true_center_x = 200
    frame = _make_comet_tail_frame(head_center=(true_center_x, 150), tail_dx=1)

    detector = RedDotDetector(DetectionSettings())
    result = detector.detect_best(frame)
    assert result.found

    # 참고용 - 단순 이진 마스크 무게중심(꼬리 포함, 쏠림 발생) 직접 계산
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    s = DetectionSettings()
    mask1 = cv2.inRange(hsv, np.array(s.hsv_lower1), np.array(s.hsv_upper1))
    mask2 = cv2.inRange(hsv, np.array(s.hsv_lower2), np.array(s.hsv_upper2))
    mask = cv2.bitwise_or(mask1, mask2)
    contour = sorted(
        cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0],
        key=cv2.contourArea,
        reverse=True,
    )[0]
    moments = cv2.moments(contour)
    naive_center_x = moments["m10"] / moments["m00"]

    naive_bias = abs(naive_center_x - true_center_x)
    detector_bias = abs(result.center_px[0] - true_center_x)
    assert naive_bias > 3.0  # 꼬리 쪽으로 유의미하게 쏠림(회귀 확인용)
    assert detector_bias < naive_bias * 0.5  # 정사각형 분할 방식으로 쏠림이 뚜렷이 줄어듦


def test_core_circle_is_round_even_when_ellipse_is_stretched_by_comet_tail():
    """core_circle은 꼬리를 뺀 밝은 "머리"만의 형상이므로, 꼬리가 길어질수록 늘어나는
    ellipse와 달리 꼬리 길이와 거의 무관하게 일정한 반지름을 유지해야 한다(실제 LED 광원
    자체는 항상 원형이라는 물리적 사실과 일치 - docs/detection_notes.md 10차 참고)."""
    detector = RedDotDetector(DetectionSettings())

    short_tail = _make_comet_tail_frame(head_center=(200, 150), tail_len=5)
    long_tail = _make_comet_tail_frame(head_center=(200, 150), tail_len=20)

    short_result = detector.detect_best(short_tail)
    long_result = detector.detect_best(long_tail)
    assert short_result.found and long_result.found
    assert short_result.core_circle is not None and long_result.core_circle is not None

    # ellipse(꼬리 포함)는 꼬리가 길어지면 뚜렷이 늘어난다(회귀 확인용).
    short_major = max(short_result.ellipse[1])
    long_major = max(long_result.ellipse[1])
    assert long_major > short_major * 1.3

    # core_circle 반지름은 꼬리 길이와 거의 무관하게 유지되어야 한다.
    short_radius = short_result.core_circle[1]
    long_radius = long_result.core_circle[1]
    assert abs(long_radius - short_radius) < short_radius * 0.3


def test_no_dot_returns_not_found():
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    frame[..., 1] = 60
    detector = RedDotDetector(DetectionSettings())
    result = detector.detect_best(frame)
    assert not result.found


def test_blob_tracker_picks_nearest_to_previous():
    detector = RedDotDetector(DetectionSettings())
    tracker = BlobTracker(max_jump_px=30)

    # 1프레임: 하나의 후보
    frame1 = _make_frame(dot_center=(100, 100))
    r1 = tracker.select(detector.detect(frame1))
    assert r1.found
    assert abs(r1.center_px[0] - 100) < 2

    # 2프레임: 실제 대상은 조금 이동, 노이즈성 후보(멀리 있는 것)도 추가
    frame2 = _make_frame(dot_center=(110, 105))
    cv2.circle(frame2, (350, 250), 6, (40, 160, 230), -1)  # 멀리 있는 노이즈 블롭
    r2 = tracker.select(detector.detect(frame2))
    assert r2.found
    assert abs(r2.center_px[0] - 110) < 4
    assert abs(r2.center_px[1] - 105) < 4


def _detection(cx: float, cy: float, major: float, minor: float):
    return DetectionResult(
        found=True,
        center_px=(cx, cy),
        ellipse=((cx, cy), (minor, major), 0.0),
        area_px2=major * minor,
    )


def test_blob_tracker_blends_toward_prediction_only_when_elongated():
    """정상적인 원형 구간(elongation < threshold)의 측정치는 그대로 신뢰하고, 심한
    코멧테일 왜곡 구간(elongation >= threshold)에서만 이전 속도 기반 예측치와 blend해
    단일 프레임 흔들림을 억제해야 한다(사용자 확인 사항, 2026-09-13)."""
    tracker = BlobTracker(max_jump_px=60, elongation_correction_threshold=1.5, elongation_correction_blend=0.5)

    tracker.select([_detection(100, 100, 8, 8)])  # 1프레임: 시드
    tracker.select([_detection(110, 100, 8, 8)])  # 2프레임: 속도(+10, 0) 확립

    # 3프레임: 측정치가 25px 튀었지만(노이즈) 심하게 늘어진 형상(elongation=3.75)
    r3 = tracker.select([_detection(135, 100, 30, 8)])
    predicted_x = 120.0  # 110 + (110-100)
    assert abs(r3.center_px[0] - 127.5) < 0.5  # 측정치(135)와 예측치(120)의 중간으로 보정됨
    assert r3.center_px[0] != 135.0


def test_blob_tracker_correction_does_not_compound_across_consecutive_elongated_frames():
    """여러 프레임 연속으로 elongation이 임계값을 넘으면(코멧테일 구간이 몇 프레임 이어지는
    실측 사례), 예측이 "이전 보정값"이 아니라 항상 "이전 원시 측정치"를 근거로 계산되어야
    한다 - 그렇지 않으면 매 프레임 오차가 누적되어 결과가 실제 위치에서 점점 멀어진다
    (실측으로 확인된 회귀 버그, docs/detection_notes.md 12차 후속 수정)."""
    tracker = BlobTracker(max_jump_px=200, elongation_correction_threshold=1.5, elongation_correction_blend=0.5)

    tracker.select([_detection(1629, 444, 8, 8)])  # 정상 원형 구간
    # 아래는 실측(frame 19~22)을 단순화한 패턴: 측정치가 예측(등속 외삽) 대비 계속 "덜 이동"함
    # (감속하며 목표에 다가가는 흔한 패턴) - 매 프레임 원시 측정치를 근거로 예측해야 함.
    r1 = tracker.select([_detection(1624, 408, 30, 8)])   # elongation=3.75
    r2 = tracker.select([_detection(1605, 370, 30, 8)])
    r3 = tracker.select([_detection(1602, 351, 30, 8)])
    r4 = tracker.select([_detection(1603, 347, 30, 8)])  # 실제로는 거의 안 움직임(방향 반전 근처)

    # 마지막 결과가 원시 측정치(1603, 347)에서 크게 벗어나면 안 된다 - 누적 드리프트
    # 회귀 확인용(수정 전에는 여러 프레임 연속 보정으로 원시치에서 10px 이상 벗어났었음).
    assert math.hypot(r4.center_px[0] - 1603, r4.center_px[1] - 347) < 8.5


def test_blob_tracker_does_not_blend_circular_dot():
    """elongation이 임계값 미만(정상 원형)이면 측정치를 그대로 반환해야 한다."""
    tracker = BlobTracker(max_jump_px=60, elongation_correction_threshold=1.5, elongation_correction_blend=0.5)

    tracker.select([_detection(100, 100, 8, 8)])
    tracker.select([_detection(110, 100, 8, 8)])

    r3 = tracker.select([_detection(135, 100, 8, 8)])  # 같은 튐이지만 원형
    assert abs(r3.center_px[0] - 135.0) < 1e-9


def test_blob_tracker_rejects_jump_beyond_max():
    detector = RedDotDetector(DetectionSettings())
    tracker = BlobTracker(max_jump_px=10)

    frame1 = _make_frame(dot_center=(100, 100))
    tracker.select(detector.detect(frame1))

    frame2 = _make_frame(dot_center=(300, 250))  # 너무 멀리 뜀 (실제로는 불가능한 이동)
    r2 = tracker.select(detector.detect(frame2))
    assert not r2.found
