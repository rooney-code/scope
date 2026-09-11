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
