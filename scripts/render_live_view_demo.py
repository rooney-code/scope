"""실제 프로그램의 LiveFeedView가 그대로 그리는 화면을 캡처해서 이미지로 저장.

임의의 별도 시각화 코드가 아니라 app/views/live_feed_view.py의 실제 렌더링 로직
(_render, _crop_and_resize_around_origin, _draw_offset_text, draw_coordinate_axes,
draw_dot_guide_lines)을 그대로 통과시켜 만든 결과이므로, 실제 프로그램 화면과 100% 동일하다.

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

# 아래 값은 자동 추정(snap_tick 1점 + refine_scale 다중눈금 최소자승) 결과를 시작점으로,
# 고해상도 원본 사진에서 ±35MOA 양끝 눈금을 사람이 직접 눈으로 대조하며 화살표(0.5px)/
# 스케일(0.01) 미세조정으로 최종 확정한 값이다(사용자 확인 완료, 2026-09-11). 이 사진은
# 좌우 조명이 비대칭이라(오른쪽 눈금은 뚜렷, 왼쪽은 흐릿) 자동 추정만으로는 35MOA 근처의
# 오차를 완전히 없애지 못했음 - PixelAngleCalibration.nudge_origin()/nudge_scale()로
# 사람이 최종 확정하는 것이 실제 운영 절차임을 보여주는 예시이기도 하다.
# 자동 추정 대비 조정량: 원점 +2.216px(우측), px_per_moa 9.29 -> 9.35 (X/Y 동일 적용).
CONFIRMED_PX_PER_MOA = 9.35
CONFIRMED_ORIGIN_NUDGE_PX = 2.216

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
    calibration.profile.px_per_moa_x = CONFIRMED_PX_PER_MOA
    calibration.profile.px_per_moa_y = CONFIRMED_PX_PER_MOA
    calibration.nudge_origin(dx_px=CONFIRMED_ORIGIN_NUDGE_PX)
    print(
        f"[캘리브레이션] 최종 확정값 적용: origin={calibration.profile.origin_px_x:.3f}, "
        f"px_per_moa={calibration.profile.px_per_moa_x:.3f}"
    )

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
            # widget을 show()하지 않으면 레이아웃이 계산되지 않아 내부 QLabel이 작은
            # 기본 크기(setMinimumSize)에 머물러, 최종 스케일 축소가 지나치게 커져 얇은
            # 선이 사라지는 문제가 있었음 - 라벨 크기를 직접 지정해 방지.
            view._image_label.resize(1600, 1600)
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
