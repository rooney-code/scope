import numpy as np
import cv2

from core.vision.red_dot_detector import RedDotDetector
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


def test_comet_tail_shape_center_is_pulled_toward_bright_head_not_binary_centroid():
    """35MOA 부근에서 실측된 "코멧테일" 왜곡(고객 제공 영상으로 확인) - 이진 마스크의 단순
    무게중심(모든 픽셀 동일 가중치)은 어두운 꼬리 쪽으로 쏠리므로, 밝기 가중 무게중심이 그
    쏠림을 뚜렷이 줄여 참 중심(밝은 머리)에 더 가까워야 한다."""
    true_center_x = 200
    frame = _make_comet_tail_frame(head_center=(true_center_x, 150), tail_dx=1)

    naive = RedDotDetector(DetectionSettings(centroid_intensity_power=0.0))
    weighted = RedDotDetector(DetectionSettings(centroid_intensity_power=2.0))

    naive_result = naive.detect_best(frame)
    weighted_result = weighted.detect_best(frame)
    assert naive_result.found and weighted_result.found

    naive_bias = abs(naive_result.center_px[0] - true_center_x)
    weighted_bias = abs(weighted_result.center_px[0] - true_center_x)
    assert naive_bias > 3.0  # 꼬리 쪽으로 유의미하게 쏠림(회귀 확인용)
    assert weighted_bias < naive_bias * 0.7  # 가중치 적용으로 쏠림이 뚜렷이 줄어듦


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


def test_blob_tracker_rejects_jump_beyond_max():
    detector = RedDotDetector(DetectionSettings())
    tracker = BlobTracker(max_jump_px=10)

    frame1 = _make_frame(dot_center=(100, 100))
    tracker.select(detector.detect(frame1))

    frame2 = _make_frame(dot_center=(300, 250))  # 너무 멀리 뜀 (실제로는 불가능한 이동)
    r2 = tracker.select(detector.detect(frame2))
    assert not r2.found
