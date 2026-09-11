import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

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


def _add_vignette(frame: np.ndarray, strength: float = 60.0) -> np.ndarray:
    """가장자리로 갈수록 어두워지는 비네팅을 합성 - 실제 카메라 사진에서 무게중심 기반
    원점 추정이 배경 기울기에 편향되는 문제를 재현하기 위함."""
    h, w = frame.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = h / 2, w / 2
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    dist = dist / dist.max()
    darken = (dist * strength).astype(np.int16)
    out = frame.astype(np.int16)
    out -= darken[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def test_grid_auto_detector_subpixel_refinement_survives_vignette():
    """비네팅(가장자리 어두움)이 있어도 서브픽셀 정밀 원점 추정이 크게 흔들리지 않아야 한다 -
    실측 결과 배경 기울기 제거(detrend) 없이는 여기서 1px 이상 편향이 발생했었음."""
    frame = _make_grid_frame(width=1600, height=1200, origin=(823, 611), px_per_unit=6.0)
    frame = _add_vignette(frame, strength=50.0)

    detector = GridAutoDetector(min_line_length_ratio=0.2)
    result = detector.detect(frame)

    assert result.found
    ox, oy = result.origin_px
    assert abs(ox - 823.0) < 2.0
    assert abs(oy - 611.0) < 2.0


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


def _make_frame_with_minor_ticks(width=1600, height=400, origin_x=800, origin_y=200, px_per_unit=9.1):
    """1단위 간격의 보조눈금이 인쇄된 가로축을 흉내낸 합성 이미지 (refine_scale 검증용)."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[..., 1] = 60
    cv2.line(frame, (0, origin_y), (width, origin_y), (150, 150, 150), 1)
    for k in range(-40, 41):
        if k == 0:
            continue
        x = int(round(origin_x + k * px_per_unit))
        if 0 <= x < width:
            # tick_strip()의 기본 탐색 밴드(axis_coord-11 ~ axis_coord-8)에 닿도록
            # 충분히 길게 그림 (실제 사진의 tick도 이 정도로 눈금선 위로 뻗어 있음)
            cv2.line(frame, (x, origin_y - 14), (x, origin_y + 4), (150, 150, 150), 1)
    return frame


def test_refine_scale_corrects_single_point_snap_error():
    """snap_tick()의 클릭 1점 측정에 오차가 섞여도(예: 실제 9.1인데 9.4로 잘못 측정),
    주변 보조눈금(1단위 간격) 다수를 정밀 측정해 최소자승으로 맞추면 참값에 훨씬 가까워져야
    한다 - 실측(35MOA 근처에서 눈금이 벌어짐)으로 확인된 문제에 대한 회귀 테스트."""
    true_px_per_unit = 9.1
    frame = _make_frame_with_minor_ticks(px_per_unit=true_px_per_unit)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    from core.calibration.grid_auto_detector import GridDetectionResult

    calib = PixelAngleCalibration()
    calib.seed_from_auto_detection("CAM-REFINE", GridDetectionResult(found=True, origin_px=(800.0, 200.0)))
    calib.profile.px_per_moa_x = 9.4  # 일부러 부정확한 초기값(단일 지점 스냅의 오차 흉내)

    ok = calib.refine_scale(gray, axis="x", tick_unit_moa=1.0)

    assert ok
    assert abs(calib.profile.px_per_moa_x - true_px_per_unit) < 0.05
    # 초기값(9.4)보다 참값(9.1)에 훨씬 가까워졌는지 확인
    assert abs(calib.profile.px_per_moa_x - true_px_per_unit) < abs(9.4 - true_px_per_unit)


def test_nudge_scale_adjusts_px_per_moa_directly():
    """작업자가 눈으로 보며 눈금 간격(px-per-MOA)을 직접 미세조정하는 기능 - refine_scale()의
    자동 추정을 믿기 어려운 상황(조명 비대칭 등, 실측으로 확인됨)에서 사람이 최종 확정하는
    용도. 원점은 건드리지 않고 px_per_moa만 더해져야 한다."""
    calib = PixelAngleCalibration()
    from core.calibration.grid_auto_detector import GridDetectionResult

    calib.seed_from_auto_detection("CAM-NUDGE", GridDetectionResult(found=True, origin_px=(500.0, 300.0)))
    calib.profile.px_per_moa_x = 9.29
    calib.profile.px_per_moa_y = 9.29

    calib.nudge_scale(delta_x=0.06, delta_y=-0.02)

    assert abs(calib.profile.px_per_moa_x - 9.35) < 1e-9
    assert abs(calib.profile.px_per_moa_y - 9.27) < 1e-9
    # 원점은 그대로여야 함
    assert calib.profile.origin_px_x == 500.0
    assert calib.profile.origin_px_y == 300.0


def test_nudge_scale_without_profile_raises():
    calib = PixelAngleCalibration()
    with pytest.raises(RuntimeError):
        calib.nudge_scale(delta_x=0.01)


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
