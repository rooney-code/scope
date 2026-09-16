import pytest
from PySide6.QtWidgets import QApplication

from app.views.stage1_alignment_view import Stage1AlignmentView, _NO_CALIBRATION_MSG, _SCALE_UNCONFIRMED_MSG
from core.calibration.grid_auto_detector import GridDetectionResult
from core.calibration.pixel_angle_calibration import PixelAngleCalibration
from core.vision.red_dot_detector import DetectionResult


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_view() -> Stage1AlignmentView:
    # 최상위 위젯은 .show() 전까지 항상 isVisible()==False다(자식의 setVisible(True)
    # 여부와 무관) - 경고 라벨 표시 여부를 isVisible()로 검증하려면 실제로 보여야 한다.
    view = Stage1AlignmentView()
    view.show()
    return view


def test_warning_shown_when_no_calibration_set():
    """캘리브레이션 자체가 주입되지 않았으면(카메라 연결 직후 등) 원점을 못 찾았다는
    경고가 보여야 한다 - 화면은 정상처럼 보이는데 실제로는 오차 계산이 안 되는 상황을
    사용자가 눈치채지 못했던 문제에 대한 회귀 테스트(2026-09-14)."""
    view = _make_view()
    assert view.calibration_warning_label.isVisible()
    assert view.calibration_warning_label.text() == _NO_CALIBRATION_MSG

    view.on_detection(DetectionResult(found=True, center_px=(10.0, 10.0)))
    assert view.calibration_warning_label.isVisible()
    assert view.calibration_warning_label.text() == _NO_CALIBRATION_MSG


def test_warning_shown_when_scale_unconfirmed():
    """그리드 자동 검출로 원점만 찾고(px_per_moa가 아직 임시값 1.0) 클릭 스냅을 안 했으면,
    "스케일 미확정" 경고를 보여야 한다(실측으로 확인된 문제: 이 상태로 방치하면 터무니없는
    MOA 숫자가 나옴, 2026-09-14)."""
    view = _make_view()
    calib = PixelAngleCalibration()
    calib.seed_from_auto_detection("CAM", GridDetectionResult(found=True, origin_px=(100.0, 100.0)))
    view.set_calibration(calib)

    view.on_detection(DetectionResult(found=True, center_px=(110.0, 100.0)))

    assert view.calibration_warning_label.isVisible()
    assert view.calibration_warning_label.text() == _SCALE_UNCONFIRMED_MSG


def test_warning_hidden_once_fully_calibrated():
    """원점 + 양쪽 축 스케일이 모두 확정되면 경고가 사라져야 한다."""
    view = _make_view()
    calib = PixelAngleCalibration()
    calib.seed_from_auto_detection("CAM", GridDetectionResult(found=True, origin_px=(100.0, 100.0)))
    calib.snap_tick(tick_px=160.0, known_value=10.0, unit="moa", axis="x")
    calib.snap_tick(tick_px=40.0, known_value=10.0, unit="moa", axis="y")
    view.set_calibration(calib)

    view.on_detection(DetectionResult(found=True, center_px=(160.0, 100.0)))

    assert not view.calibration_warning_label.isVisible()
