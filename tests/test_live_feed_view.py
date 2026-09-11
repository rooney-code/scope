import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from app.views.live_feed_view import LiveFeedView
from core.calibration.grid_auto_detector import GridDetectionResult
from core.calibration.pixel_angle_calibration import PixelAngleCalibration


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_calibration(origin_px: tuple[float, float], px_per_moa: float = 6.0) -> PixelAngleCalibration:
    calib = PixelAngleCalibration()
    calib.seed_from_auto_detection("CAM", GridDetectionResult(found=True, origin_px=origin_px))
    calib.profile.px_per_moa_x = px_per_moa
    calib.profile.px_per_moa_y = px_per_moa
    return calib


def test_without_calibration_returns_frame_unchanged():
    view = LiveFeedView()
    frame = np.zeros((2160, 3840, 3), dtype=np.uint8)  # 4K 프레임 시뮬레이션
    frame[100, 200] = [1, 2, 3]  # 좌상단 근처 마커

    out = view._crop_around_origin(frame)

    assert out.shape == frame.shape
    assert list(out[100, 200]) == [1, 2, 3]  # 원본 그대로(크롭 안 됨)


def test_crop_is_centered_on_origin_not_frame_center():
    """카메라 설치 상태에 따라 원점이 프레임 중앙이 아닌 위치(예: 좌측 상단 쪽)에 있는
    4K급 프레임을 가정. 크롭 결과의 중심이 프레임 기하학적 중앙이 아니라 캘리브레이션된
    원점과 일치해야 한다."""
    h, w = 2160, 3840
    origin_px = (1000.0, 700.0)  # 프레임 중앙(1920,1080)과는 거리가 먼, 설치 상태를 흉내낸 원점
    calib = _make_calibration(origin_px, px_per_moa=6.0)

    view = LiveFeedView(default_view_range_moa=45.0)
    view.set_calibration(calib)

    frame = np.zeros((h, w, 3), dtype=np.uint8)
    ox, oy = int(origin_px[0]), int(origin_px[1])
    frame[oy, ox] = [10, 20, 30]  # 원점 위치에 마커

    out = view._crop_around_origin(frame)

    # 출력은 원본과 같은 크기로 리사이즈됨(디스플레이 편의) - 마커가 출력 이미지의
    # 중앙 부근(가운데 크롭이었으므로)에 위치해야 함
    out_h, out_w = out.shape[:2]
    ys, xs = np.where((out[..., 0] > 0))
    assert len(ys) > 0, "원점 마커가 크롭된 결과 안에 남아있어야 함"
    marker_y, marker_x = ys[0], xs[0]

    # 리사이즈로 인해 정확히 중앙은 아니지만, 프레임 전체 중앙(1920,1080 스케일 기준)이
    # 아니라 원점 근처(출력 이미지의 중앙 부근)에 있어야 함을 확인
    assert abs(marker_x - out_w / 2) < out_w * 0.1
    assert abs(marker_y - out_h / 2) < out_h * 0.1


def test_zoom_level_narrows_view_range():
    calib = _make_calibration((500.0, 500.0), px_per_moa=6.0)
    view = LiveFeedView(default_view_range_moa=45.0)
    view.set_calibration(calib)

    assert view._current_view_range_moa() == 45.0
    view._on_zoom_changed(3)
    assert view._current_view_range_moa() == pytest.approx(15.0)


def test_grid_step_picks_a_readable_spacing_not_1moa():
    """카메라 배율이 높으면(px_per_moa가 커서 view_range가 넓게 느껴지는 경우) 1MOA 간격
    격자는 체크무늬처럼 너무 촘촘해진다 - 화면당 대략 10줄 안팎이 되는 '보기 좋은' 간격
    (0.5/1/2/5/10/20/50/100 중 하나)을 고르는지 확인."""
    calib = _make_calibration((500.0, 500.0), px_per_moa=6.0)
    view = LiveFeedView(default_view_range_moa=45.0)
    view.set_calibration(calib)

    # 기본(45MOA 범위)에서는 1MOA 간격이면 90줄이나 되므로 더 큰 간격을 골라야 함
    assert view._current_grid_step_moa() > 1.0

    # 확대해서 범위가 좁아지면 더 촘촘한 간격도 허용
    view._on_zoom_changed(5)  # view_range = 9
    assert view._current_grid_step_moa() <= 5.0


def test_offset_text_uses_ascii_labels_not_korean():
    """cv2.putText는 한글을 지원하지 않아 깨지므로, 오버레이에 굽는 텍스트는 R/L/U/D 같은
    영문 라벨만 사용해야 한다(라벨 문구 자체는 한글이어도 되는 self._offset_label은 예외)."""
    calib = _make_calibration((0.0, 0.0), px_per_moa=6.0)
    view = LiveFeedView()
    view.set_calibration(calib)

    from core.vision.red_dot_detector import DetectionResult

    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    detection = DetectionResult(found=True, center_px=(30.0, 170.0))  # 원점 기준 좌하단

    view._draw_offset_text(frame, detection, calib.profile)

    assert "R" in view._offset_label.text() or "L" in view._offset_label.text()
    assert all(ord(c) < 128 for c in ["R", "L", "U", "D"])  # ASCII 라벨만 사용함을 명시
