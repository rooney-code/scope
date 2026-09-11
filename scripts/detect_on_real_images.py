"""실제 현장 캡처 이미지(tests/test_images/originals/)로 그리드 자동검출 + 레드닷 검출이
정상 동작하는지 빠르게 확인하는 스크립트 (Qt 없이, 콘솔 출력만).

화면에 실제로 어떻게 보이는지(오버레이 포함)까지 확인하려면 scripts/render_live_view_demo.py를
사용할 것 - 그 스크립트는 실제 LiveFeedView를 그대로 렌더링하므로 여기서는 검출 로직 자체가
정상 동작하는지만 빠르게 훑어보는 용도로 남겨둔다.

실행: PYTHONPATH=. python3 scripts/detect_on_real_images.py
"""
from __future__ import annotations

from pathlib import Path

import cv2

from core.calibration.grid_auto_detector import GridAutoDetector
from core.calibration.pixel_angle_calibration import PixelAngleCalibration
from core.config.settings import DetectionSettings
from core.vision.red_dot_detector import RedDotDetector

ORIGINALS_DIR = Path(__file__).resolve().parent.parent / "tests" / "test_images" / "originals"

BRIGHT_CALIBRATION_IMAGE = "조리개최대.jpg"
RED_DOT_IMAGES = ["8단계.jpg", "좌하단_7단계.jpg", "7단계_원점근처.jpg"]


def main() -> None:
    bright_path = ORIGINALS_DIR / BRIGHT_CALIBRATION_IMAGE
    bright = cv2.imread(str(bright_path))
    if bright is None:
        raise FileNotFoundError(f"조리개 최대 이미지를 찾을 수 없습니다: {bright_path}")

    grid_result = GridAutoDetector().detect(bright)
    print(f"[그리드 검출] found={grid_result.found}, origin_px={grid_result.origin_px}")
    if not grid_result.found:
        print("그리드 원점 검출 실패 - GridAutoDetector 파라미터를 재조정해야 합니다.")
        return

    gray_bright = cv2.cvtColor(bright, cv2.COLOR_BGR2GRAY)
    calib = PixelAngleCalibration()
    calib.seed_from_auto_detection("DEMO", grid_result)
    # 실제 작업 흐름과 동일하게: 작업자가 10mrad 위치의 tick 하나를 클릭 스냅해 초기
    # px_per_moa를 잡은 뒤, refine_scale()로 근처 보조눈금 다수를 이용해 정밀화한다.
    calib.snap_tick(tick_px=1921.0, known_value=10.0, unit="mrad", axis="x")
    calib.profile.px_per_moa_y = calib.profile.px_per_moa_x
    ok_x = calib.refine_scale(gray_bright, axis="x")
    print(
        f"[정밀 스케일 보정 (조리개최대, X축)] 성공={ok_x}, "
        f"사용된 보조눈금 개수={calib.last_refine_tick_count}, "
        f"px_per_moa_x={calib.profile.px_per_moa_x:.4f}"
    )
    ok_y = calib.refine_scale(gray_bright, axis="y")
    print(
        f"[정밀 스케일 보정 (조리개최대, Y축)] 성공={ok_y}, "
        f"사용된 보조눈금 개수={calib.last_refine_tick_count}, "
        f"px_per_moa_y={calib.profile.px_per_moa_y:.4f}"
    )

    dot_detector = RedDotDetector(DetectionSettings())
    for name in RED_DOT_IMAGES:
        path = ORIGINALS_DIR / name
        img = cv2.imread(str(path))
        if img is None:
            print(f"이미지를 찾을 수 없어 건너뜀: {path}")
            continue
        result = dot_detector.detect_best(img)
        print(f"[레드닷 검출] {name}: found={result.found}, center_px={result.center_px if result.found else None}")


if __name__ == "__main__":
    main()
