"""실제 현장 캡처 이미지(tests/test_images/originals/)로 그리드 자동검출 + 레드닷 검출을 돌려보고
결과를 오버레이 이미지로 저장한다 (tests/test_images/results/).

새로운 현장 사진으로 검출 품질을 다시 확인하고 싶을 때:
1. tests/test_images/originals/ 에 새 이미지를 넣는다 (조리개 최대 상태 1장 필수,
   레드닷이 보이는 사진은 몇 장이든).
2. 아래 IMAGE_SETS 딕셔너리를 새 파일명에 맞게 수정한다.
3. `python scripts/detect_on_real_images.py` 실행.

실행: PYTHONPATH=. python3 scripts/detect_on_real_images.py
"""
from __future__ import annotations

from pathlib import Path

import cv2

from core.calibration.grid_auto_detector import GridAutoDetector
from core.config.settings import DetectionSettings
from core.vision.red_dot_detector import DetectionResult, RedDotDetector

ORIGINALS_DIR = Path(__file__).resolve().parent.parent / "tests" / "test_images" / "originals"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "tests" / "test_images" / "results"

BRIGHT_CALIBRATION_IMAGE = "조리개최대.jpg"
RED_DOT_IMAGES = ["8단계.jpg", "좌하단_7단계.jpg"]


def draw_overlay(img, origin_px: tuple[float, float], dot_result: DetectionResult, title: str):
    out = img.copy()
    h, w = out.shape[:2]
    ox, oy = int(origin_px[0]), int(origin_px[1])

    # 검출된 원점을 기준으로 전체 폭/높이에 걸쳐 십자선을 다시 그림 (자홍색)
    cv2.line(out, (0, oy), (w, oy), (255, 0, 255), 3)
    cv2.line(out, (ox, 0), (ox, h), (255, 0, 255), 3)
    cv2.circle(out, (ox, oy), 18, (255, 0, 255), 4)
    cv2.putText(out, "GRID ORIGIN", (ox + 25, oy - 15), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 0, 255), 3)

    if dot_result.found:
        dx, dy = dot_result.center_px
        dxi, dyi = int(dx), int(dy)
        if dot_result.ellipse is not None:
            cv2.ellipse(out, dot_result.ellipse, (0, 255, 0), 4)
        cv2.drawMarker(out, (dxi, dyi), (0, 255, 0), cv2.MARKER_CROSS, 40, 4)
        cv2.putText(out, "RED DOT", (dxi + 25, dyi + 40), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 3)
        offset_px = (dx - ox, dy - oy)
        cv2.putText(
            out,
            f"offset px: ({offset_px[0]:.0f}, {offset_px[1]:.0f})",
            (30, h - 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.3,
            (0, 255, 255),
            3,
        )

    cv2.putText(out, title, (30, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (255, 255, 255), 4)
    return out


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    bright_path = ORIGINALS_DIR / BRIGHT_CALIBRATION_IMAGE
    bright = cv2.imread(str(bright_path))
    if bright is None:
        raise FileNotFoundError(f"조리개 최대 이미지를 찾을 수 없습니다: {bright_path}")

    grid_detector = GridAutoDetector()
    grid_result = grid_detector.detect(bright)
    print(f"[그리드 검출] found={grid_result.found}, origin_px={grid_result.origin_px}")
    if not grid_result.found:
        print("그리드 원점 검출 실패 - GridAutoDetector 파라미터를 재조정해야 합니다.")
        return

    dot_detector = RedDotDetector(DetectionSettings())

    bright_dot = dot_detector.detect_best(bright)
    out = draw_overlay(bright, grid_result.origin_px, bright_dot, "Bright/Calibration")
    out_path = RESULTS_DIR / f"1_{BRIGHT_CALIBRATION_IMAGE}"
    cv2.imwrite(str(out_path), out, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f"저장: {out_path}")

    for i, name in enumerate(RED_DOT_IMAGES, start=2):
        path = ORIGINALS_DIR / name
        img = cv2.imread(str(path))
        if img is None:
            print(f"이미지를 찾을 수 없어 건너뜀: {path}")
            continue
        result = dot_detector.detect_best(img)
        print(f"[레드닷 검출] {name}: found={result.found}, center_px={result.center_px if result.found else None}")
        overlay = draw_overlay(img, grid_result.origin_px, result, name)
        out_path = RESULTS_DIR / f"{i}_{name}"
        cv2.imwrite(str(out_path), overlay, [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(f"저장: {out_path}")


if __name__ == "__main__":
    main()
