import tempfile
from pathlib import Path

import cv2
import numpy as np

from core.calibration.grid_auto_detector import GridAutoDetector
from core.calibration.pixel_angle_calibration import PixelAngleCalibration


def _make_grid_frame(width=800, height=600, origin=(400, 300), px_per_unit=6.0):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[..., 1] = 60
    ox, oy = origin
    cv2.line(frame, (0, oy), (width, oy), (200, 200, 200), 2)
    cv2.line(frame, (ox, 0), (ox, height), (200, 200, 200), 2)
    # tick marks every 10 "unit"
    for m in range(-30, 31, 10):
        if m == 0:
            continue
        tx = int(ox + m * px_per_unit)
        ty = int(oy - m * px_per_unit)
        cv2.line(frame, (tx, oy - 6), (tx, oy + 6), (200, 200, 200), 2)
        cv2.line(frame, (ox - 6, ty), (ox + 6, ty), (200, 200, 200), 2)
    return frame


def test_grid_auto_detector_finds_origin():
    frame = _make_grid_frame(origin=(400, 300))
    detector = GridAutoDetector(min_line_length_ratio=0.3)
    result = detector.detect(frame)
    assert result.found
    ox, oy = result.origin_px
    assert abs(ox - 400) < 5
    assert abs(oy - 300) < 5


def test_pixel_angle_calibration_snap_and_convert():
    calib = PixelAngleCalibration(mrad_to_moa_ratio=3.438)

    from core.calibration.grid_auto_detector import GridDetectionResult

    seed = GridDetectionResult(found=True, origin_px=(400.0, 300.0))
    calib.seed_from_auto_detection("CAM-1", seed)

    # 10 unit(가상 MOA) tick이 60px 떨어져 있다고 가정하고 스냅
    calib.snap_tick(tick_px=460.0, known_value=10.0, unit="moa", axis="x")
    calib.snap_tick(tick_px=240.0, known_value=10.0, unit="moa", axis="y")

    assert abs(calib.profile.px_per_moa_x - 6.0) < 1e-6
    assert abs(calib.profile.px_per_moa_y - 6.0) < 1e-6

    # 원점에서 +60px x이동, -60px(화면 위쪽) y이동 => (10 moa, +10 moa)
    x_moa, y_moa = calib.to_moa((460.0, 240.0))
    assert abs(x_moa - 10.0) < 1e-6
    assert abs(y_moa - 10.0) < 1e-6


def test_pixel_angle_calibration_mrad_conversion():
    calib = PixelAngleCalibration(mrad_to_moa_ratio=3.438)
    from core.calibration.grid_auto_detector import GridDetectionResult

    seed = GridDetectionResult(found=True, origin_px=(0.0, 0.0))
    calib.seed_from_auto_detection("CAM-2", seed)

    # 10 mrad tick이 100px 떨어짐 => 10mrad = 34.38 moa
    calib.snap_tick(tick_px=100.0, known_value=10.0, unit="mrad", axis="x")
    expected_px_per_moa = 100.0 / 34.38
    assert abs(calib.profile.px_per_moa_x - expected_px_per_moa) < 1e-3


def test_calibration_save_and_load_roundtrip():
    calib = PixelAngleCalibration()
    from core.calibration.grid_auto_detector import GridDetectionResult

    seed = GridDetectionResult(found=True, origin_px=(123.0, 456.0))
    calib.seed_from_auto_detection("CAM-XYZ", seed)
    calib.snap_tick(tick_px=183.0, known_value=10.0, unit="moa", axis="x")

    with tempfile.TemporaryDirectory() as tmp:
        calib.save(tmp)

        calib2 = PixelAngleCalibration()
        loaded = calib2.load(tmp, "CAM-XYZ")
        assert loaded
        assert calib2.profile.origin_px_x == 123.0
        assert abs(calib2.profile.px_per_moa_x - 6.0) < 1e-6

        assert calib2.load(tmp, "NON_EXISTENT") is False
