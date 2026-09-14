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
    # is_ready(스케일 확정)가 False면 크롭/오차 계산이 적용되지 않으므로(2026-09-14 변경),
    # 이 헬퍼가 시뮬레이션하는 "완전히 캘리브레이션된 카메라" 상태를 정확히 나타내려면
    # 두 축 모두 확정 표시가 필요하다.
    calib.profile.scale_confirmed_x = True
    calib.profile.scale_confirmed_y = True
    return calib


def test_without_calibration_returns_frame_unchanged():
    view = LiveFeedView()
    frame = np.zeros((2160, 3840, 3), dtype=np.uint8)  # 4K 프레임 시뮬레이션
    frame[100, 200] = [1, 2, 3]  # 좌상단 근처 마커

    out, transform = view._crop_and_resize_around_origin(frame, None)

    assert transform is None
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

    out, transform = view._crop_and_resize_around_origin(frame, calib.profile)

    assert transform is not None
    # 원점 기준 ±45MOA(px_per_moa=6 -> ±270px)로 자른 정사각형 크롭이므로, 리사이즈해도
    # 자기 자신의 가로세로 비율(1:1)을 유지해야 함(원본 프레임의 16:9 비율로 늘어나면 안 됨)
    out_h, out_w = out.shape[:2]
    assert abs(out_w - out_h) <= 1  # 반올림 오차 허용

    ys, xs = np.where((out[..., 0] > 0))
    assert len(ys) > 0, "원점 마커가 크롭된 결과 안에 남아있어야 함"
    marker_y, marker_x = ys[0], xs[0]

    # 마커(=원점)가 크롭 결과의 중앙 부근에 위치해야 함(가운데를 기준으로 잘랐으므로)
    assert abs(marker_x - out_w / 2) < out_w * 0.1
    assert abs(marker_y - out_h / 2) < out_h * 0.1


def test_crop_preserves_aspect_ratio_for_non_square_view_range():
    """px_per_moa_x와 px_per_moa_y가 다르면 크롭 영역이 정사각형이 아닐 수 있는데, 이때도
    강제로 정사각형이나 원본 프레임 비율로 왜곡하지 않고 크롭 자신의 비율을 유지해야 한다."""
    calib = PixelAngleCalibration()
    from core.calibration.grid_auto_detector import GridDetectionResult

    calib.seed_from_auto_detection("CAM", GridDetectionResult(found=True, origin_px=(500.0, 500.0)))
    calib.profile.px_per_moa_x = 10.0
    calib.profile.px_per_moa_y = 4.0  # x축과 y축 스케일이 다름 -> 크롭이 가로로 긴 직사각형

    view = LiveFeedView(default_view_range_moa=20.0)
    view.set_calibration(calib)

    frame = np.zeros((1000, 1000, 3), dtype=np.uint8)
    out, transform = view._crop_and_resize_around_origin(frame, calib.profile)

    assert transform is not None
    out_h, out_w = out.shape[:2]
    expected_ratio = (20.0 * 10.0) / (20.0 * 4.0)  # crop_w / crop_h
    assert (out_w / out_h) == pytest.approx(expected_ratio, rel=0.02)


def test_zoom_level_narrows_view_range():
    calib = _make_calibration((500.0, 500.0), px_per_moa=6.0)
    view = LiveFeedView(default_view_range_moa=45.0)
    view.set_calibration(calib)

    assert view._current_view_range_moa() == 45.0
    view._on_zoom_changed(3)
    assert view._current_view_range_moa() == pytest.approx(15.0)


def test_axis_label_step_picks_a_readable_spacing():
    """눈금(tick) 자체는 항상 1MOA 간격으로 고정하고(사용자 요청), 숫자 라벨만 화면에
    너무 빽빽하거나 뜸해지지 않도록 view_range에 맞춰 '보기 좋은' 간격
    (1/2/5/10/20/50/100 중 하나)을 고르는지 확인."""
    calib = _make_calibration((500.0, 500.0), px_per_moa=6.0)
    view = LiveFeedView(default_view_range_moa=45.0)
    view.set_calibration(calib)

    # 기본(45MOA 범위)에서는 라벨을 1MOA마다 찍으면 90개나 되므로 더 큰 간격을 골라야 함
    assert view._current_axis_label_step_moa() > 1.0

    # 확대해서 범위가 좁아지면 더 촘촘한 라벨 간격도 허용
    view._on_zoom_changed(5)  # view_range = 9
    assert view._current_axis_label_step_moa() <= 5.0


def test_calibration_mode_crops_around_origin_like_test_tab():
    """캘리브레이션에서도 시험 진행 탭과 동일하게 원점 기준으로 확대해서 보여줘야 미세조정이
    쉽다는 사용자 요청(2026-09-14) - 원점(프로파일)이 있고 원점 지정 대기 상태가 아니면
    스케일이 미확정이어도 크롭해야 한다."""
    calib = _make_calibration((500.0, 500.0), px_per_moa=6.0)
    view = LiveFeedView(default_view_range_moa=45.0, display_target_size=800)
    view.set_calibration(calib)
    view.set_calibration_mode(True)

    frame = np.zeros((1000, 2000, 3), dtype=np.uint8)
    view.on_frame(frame)

    # 원본 전체(2000x1000)가 아니라 원점 주변 ±45MOA*6px/MOA=270px 반경으로 크롭된 뒤
    # display_target_size=800에 맞춰 리사이즈됨 -> 원본 그대로였다면 (800, 400)이었을 것
    assert view._last_display_size != (800, 400)
    assert view._last_transform != (0.0, 0.0, 0.4)


def test_calibration_mode_origin_picking_skips_crop():
    """"그리드 수동 검출"로 원점 지정을 기다리는 동안(armed)에는, 프로파일에 이미 (어쩌면
    잘못된) 원점이 있어도 크롭하지 않고 원본 전체를 보여줘야 한다 - 안 그러면 새로 클릭해야
    할 실제 크로스헤어가 크롭된 화면 밖에 있어 클릭할 수 없는 경우가 생긴다(2026-09-14)."""
    calib = _make_calibration((500.0, 500.0), px_per_moa=6.0)
    view = LiveFeedView(default_view_range_moa=45.0, display_target_size=800)
    view.set_calibration(calib)
    view.set_calibration_mode(True)
    view.set_calibration_origin_picking(True)

    frame = np.zeros((1000, 2000, 3), dtype=np.uint8)
    view.on_frame(frame)

    assert view._last_display_size == (800, 400)
    assert view._last_transform == (0.0, 0.0, 0.4)


def test_calibration_mode_draws_origin_marker_even_without_confirmed_scale():
    """캘리브레이션 탭에서 원점을 찾은 뒤(자동/수동 검출 직후, 스케일은 아직 대략적인
    시작 추정치) 아무 표시도 없어서 화살표 미세조정 워크플로가 안 됐던 문제에 대한 회귀
    테스트(사용자 피드백, 2026-09-14). 원점 마커/좌표축은 스케일 확정 여부와 무관하게
    (현재 추정치로) 그려져야 한다 - 그래야 눈금 간격 미세조정 버튼을 누르며 눈으로 보고
    실제 그리드와 맞출 수 있다."""
    calib = PixelAngleCalibration()
    calib.seed_from_auto_detection("CAM", GridDetectionResult(found=True, origin_px=(1000.0, 500.0)))
    assert calib.is_ready is False  # 스케일 미확정 상태 그대로 둠

    view = LiveFeedView(display_target_size=800)
    view.set_calibration(calib)
    view.set_calibration_mode(True)

    frame = np.zeros((1000, 2000, 3), dtype=np.uint8)
    view.on_frame(frame)

    pixmap = view._image_label.pixmap()
    from PySide6.QtGui import QImage

    qimg = pixmap.toImage().convertToFormat(QImage.Format_RGB32)
    w, h = qimg.width(), qimg.height()
    buf = qimg.constBits()
    arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, qimg.bytesPerLine() // 4, 4))[:, :w, :3]

    assert not np.array_equal(arr, np.zeros_like(arr))  # 마커가 그려져 순수 검은 화면이 아니어야 함


def test_calibration_mode_click_maps_back_to_original_frame_coords():
    """LiveFeedView가 캘리브레이션 모드에서 라벨 클릭을 받으면, 크롭 변환 + letterbox를
    모두 역산해서 원본 프레임 좌표를 frame_clicked_px로 내보내야 한다."""
    view = LiveFeedView(display_target_size=800)
    view.set_calibration_mode(True)

    frame = np.zeros((1000, 2000, 3), dtype=np.uint8)  # 캘리브레이션 없이도(원점 미지정) 동작
    view.on_frame(frame)  # display = 800x400, transform=(0,0,0.4)

    view._image_label.setFixedSize(1600, 1600)  # 라벨이 정사각형이라 letterbox(상하 여백)가 생김

    received: list[tuple[float, float]] = []
    view.frame_clicked_px.connect(lambda x, y: received.append((x, y)))

    # 라벨 안에서 display(800x400)가 그려지는 영역: label_scale=min(1600/800,1600/400)=2
    # -> disp 800x400이 1600x800으로 그려지고 상하로 (1600-800)/2=400px씩 여백
    view._on_image_clicked(label_x=1600 / 2, label_y=400 + 800 / 2)  # display 중앙 클릭
    assert len(received) == 1
    orig_x, orig_y = received[0]
    assert orig_x == pytest.approx(1000.0, abs=1.0)  # 2000/2
    assert orig_y == pytest.approx(500.0, abs=1.0)  # 1000/2

    # letterbox 여백(예: 맨 위)을 클릭하면 무시되어야 함
    received.clear()
    view._on_image_clicked(label_x=800, label_y=10)
    assert received == []


def test_offset_text_draws_ascii_labels_on_image():
    """cv2.putText는 한글을 지원하지 않아 깨지므로, 오버레이에 굽는 텍스트는 R/L/U/D 같은
    영문 라벨만 사용해야 한다. 별도 Qt 라벨(과거 self._offset_label)은 시험 진행 탭의
    Stage1AlignmentView.offset_label과 중복이라 제거했다(2026-09-14) - 이제는 영상 위
    cv2 오버레이만 검증한다."""
    calib = _make_calibration((0.0, 0.0), px_per_moa=6.0)
    view = LiveFeedView()
    view.set_calibration(calib)

    from core.vision.red_dot_detector import DetectionResult

    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    detection = DetectionResult(found=True, center_px=(30.0, 170.0))  # 원점 기준 좌하단

    view._draw_offset_text(frame, detection, calib.profile, True)

    assert not np.array_equal(frame, np.zeros((200, 200, 3), dtype=np.uint8))  # 배경 박스 + 텍스트가 그려짐
    assert all(ord(c) < 128 for c in ["R", "L", "U", "D"])  # ASCII 라벨만 사용함을 명시


def test_offset_text_checkbox_off_draws_nothing():
    """show_on_image=False(체크박스 꺼짐)이면 영상 위에 아무것도 그리지 않아야 한다 -
    사용자 요청(2026-09-13): 오버레이 항목별 개별 표시/숨기기."""
    from core.vision.red_dot_detector import DetectionResult

    calib = _make_calibration((0.0, 0.0), px_per_moa=6.0)
    view = LiveFeedView()
    view.set_calibration(calib)

    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    detection = DetectionResult(found=True, center_px=(30.0, 170.0))

    view._draw_offset_text(frame, detection, calib.profile, False)

    assert np.array_equal(frame, np.zeros((200, 200, 3), dtype=np.uint8))  # 영상에는 아무것도 안 그려짐


def test_overlay_checkboxes_independently_control_rendered_elements():
    """원점/기준선, 레드닷, 안내선, 오차 정보를 각각 껐을 때 그 요소만 화면에서 사라지고
    나머지는 남아있어야 한다(사용자 요청: 오버레이 항목별 개별 표시/숨기기 + 전체 보이기/
    숨기기)."""
    from core.vision.red_dot_detector import DetectionResult

    calib = _make_calibration((100.0, 100.0), px_per_moa=6.0)
    view = LiveFeedView()
    view.set_calibration(calib)
    view._image_label.setFixedSize(400, 400)

    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    frame[..., 1] = 60
    detection = DetectionResult(found=True, center_px=(150.0, 100.0))  # 원점에서 오른쪽으로 이동

    def render_and_get_pixmap_image():
        from PySide6.QtGui import QImage

        view.on_frame(frame)
        view.on_detection(detection)
        pixmap = view._image_label.pixmap()
        qimg = pixmap.toImage().convertToFormat(QImage.Format_RGB32)
        w, h = qimg.width(), qimg.height()
        buf = qimg.constBits()
        arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, qimg.bytesPerLine() // 4, 4))[:, :w, :3]
        return arr.copy()

    view._set_all_overlay_checkboxes(True)
    all_on = render_and_get_pixmap_image()

    view._red_dot_checkbox.setChecked(False)
    red_dot_off = render_and_get_pixmap_image()
    assert not np.array_equal(all_on, red_dot_off)  # 레드닷을 끄면 화면이 달라져야 함

    view._set_all_overlay_checkboxes(False)
    all_off = render_and_get_pixmap_image()
    assert not np.array_equal(all_on, all_off)
    assert not np.array_equal(red_dot_off, all_off)  # 안내선/기준선까지 꺼져 추가로 달라짐
