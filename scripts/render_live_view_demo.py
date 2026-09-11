"""실제 프로그램의 LiveFeedView가 그대로 그리는 화면을 캡처해서 이미지로 저장.

임의의 별도 시각화 코드가 아니라 app/views/live_feed_view.py의 실제 렌더링 로직
(_render, _crop_around_origin, _draw_offset_text, draw_moa_grid_overlay)을 그대로 통과시켜
만든 결과이므로, 실제 프로그램 화면과 100% 동일하다.

px_per_moa는 이 저장소의 예시 사진 한 장을 기준으로 한 임시 측정값이며, 실제 프로그램에서는
카메라별로 캘리브레이션 화면(자동검출 + 클릭스냅/화살표 미세조정)에서 사용자가 직접 확정/
재조정한다 - 여기 하드코딩된 값은 데모 전용.

실행: QT_QPA_PLATFORM=offscreen PYTHONPATH=. python3 scripts/render_live_view_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
from PySide6.QtWidgets import QApplication

from app.views.live_feed_view import LiveFeedView
from core.calibration.grid_auto_detector import GridAutoDetector
from core.calibration.pixel_angle_calibration import PixelAngleCalibration
from core.config.settings import DetectionSettings
from core.vision.blob_tracker import BlobTracker
from core.vision.red_dot_detector import RedDotDetector

ORIGINALS_DIR = Path(__file__).resolve().parent.parent / "tests" / "test_images" / "originals"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "tests" / "test_images" / "results"

BRIGHT_CALIBRATION_IMAGE = "조리개최대.jpg"
# "+10MRAD" tick의 실측 픽셀 위치(연결요소 분석으로 측정, docs/detection_notes.md 참고).
# px_per_moa_y는 y축 tick을 별도로 정밀 측정하지 못해 x축과 동일하다고 가정한 근사치 -
# 실제 프로그램에서는 캘리브레이션 화면에서 y축도 별도로 클릭 스냅해 정확히 잡아야 한다.
DEMO_10MRAD_TICK_X_PX = 1921.0

RED_DOT_IMAGES = ["8단계.jpg", "좌하단_7단계.jpg", "7단계_원점근처.jpg"]


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)

    bright = cv2.imread(str(ORIGINALS_DIR / BRIGHT_CALIBRATION_IMAGE))
    grid_result = GridAutoDetector().detect(bright)
    if not grid_result.found:
        raise RuntimeError("그리드 원점 자동검출 실패 - 조리개최대.jpg를 확인하세요.")
    print(f"[캘리브레이션] 자동검출 원점: {grid_result.origin_px}")

    calibration = PixelAngleCalibration()
    calibration.seed_from_auto_detection("DEMO-CAM", grid_result)
    calibration.snap_tick(tick_px=DEMO_10MRAD_TICK_X_PX, known_value=10.0, unit="mrad", axis="x")
    calibration.profile.px_per_moa_y = calibration.profile.px_per_moa_x  # y축 근사치(주석 참고)
    print(f"[캘리브레이션] px_per_moa: {calibration.profile.px_per_moa_x:.3f}")

    detector = RedDotDetector(DetectionSettings())
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    for name in RED_DOT_IMAGES:
        img = cv2.imread(str(ORIGINALS_DIR / name))
        if img is None:
            print(f"이미지를 찾을 수 없어 건너뜀: {name}")
            continue

        tracker = BlobTracker(max_jump_px=10_000)  # 단일 정지 이미지이므로 게이팅 불필요
        detection = tracker.select(detector.detect(img))

        for grid_on in (False, True):
            view = LiveFeedView()
            view.set_calibration(calibration)
            view.resize(1920, 1080)  # 실제 프로그램의 FHD 화면 크기
            view._grid_checkbox.setChecked(grid_on)

            view.on_frame(img)
            view.on_detection(detection)

            pixmap = view._image_label.pixmap()
            suffix = "grid_on" if grid_on else "grid_off"
            out_path = RESULTS_DIR / f"liveview_{name.rsplit('.', 1)[0]}_{suffix}.png"
            pixmap.save(str(out_path))
            print(f"저장: {out_path} (레드닷 검출: {detection.found})")


if __name__ == "__main__":
    main()
