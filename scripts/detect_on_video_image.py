"""tests/test_images/video_image/ (100프레임)에 실제 프로덕션 검출 파이프라인
(RedDotDetector + BlobTracker)과 실제 화면 렌더링(LiveFeedView)을 그대로 돌려 검출
성공률을 확인하고, 대표 프레임의 실제 프로그램 화면(좌표축/눈금/안내선 포함)을 저장한다.

이 폴더는 기존 조리개최대.jpg(캘리브레이션 기준 사진)와 동일 카메라로 촬영되었으므로,
`render_live_view_demo.py`에서 사용자가 최종 확정한 캘리브레이션 값(원점 nudge +2.216px,
px_per_moa 9.35)을 그대로 재사용한다 - 이 폴더만 다시 캘리브레이션하지 않는다.

추가로, 코멧테일 형상 프레임에서 검출기가 구하는 두 가지 형상(꼬리까지 포함해 늘어져
보이는 fitEllipse 대 밝은 코어만의 원형 core_circle)을 나란히 비교하는 진단 이미지도
만든다 - 실제 화면(LiveFeedView)에는 형상 윤곽선을 그리지 않으므로(중심점 마커만 표시),
이 비교 이미지는 검증 전용이며 실제 프로그램 화면이 아니다.

실행: QT_QPA_PLATFORM=offscreen PYTHONPATH=. python3 scripts/detect_on_video_image.py
결과: tests/test_images/video_image_results/
  - {label}_{frame}_liveview.png : 실제 LiveFeedView 렌더링 그대로(좌표축/눈금/안내선/오차텍스트)
  - {frame}_shape_compare.jpg    : ellipse(꼬리 포함) vs core_circle(코어만 원형) 비교(검증 전용)
  - detection_summary.json       : 전체 100프레임 좌표/형상 기록
"""
from __future__ import annotations

import json
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

ROOT = Path(__file__).resolve().parent.parent
ORIGINALS_DIR = ROOT / "tests" / "test_images" / "originals"
VIDEO_DIR = ROOT / "tests" / "test_images" / "video_image"
RESULTS_DIR = ROOT / "tests" / "test_images" / "video_image_results"

# render_live_view_demo.py의 최종 확정값과 동일 (같은 카메라 - 재캘리브레이션 불필요)
CONFIRMED_PX_PER_MOA = 9.35
CONFIRMED_ORIGIN_NUDGE_PX = 2.216


def _build_calibration() -> PixelAngleCalibration:
    bright = cv2.imread(str(ORIGINALS_DIR / "조리개최대.jpg"))
    grid_result = GridAutoDetector().detect(bright)
    if not grid_result.found:
        raise RuntimeError("그리드 원점 자동검출 실패 - 조리개최대.jpg를 확인하세요.")

    calibration = PixelAngleCalibration()
    calibration.seed_from_auto_detection("DEMO-CAM", grid_result)
    calibration.profile.px_per_moa_x = CONFIRMED_PX_PER_MOA
    calibration.profile.px_per_moa_y = CONFIRMED_PX_PER_MOA
    calibration.nudge_origin(dx_px=CONFIRMED_ORIGIN_NUDGE_PX)
    return calibration


def _render_liveview(app: QApplication, img, detection, out_path: Path) -> None:
    view = LiveFeedView()
    view.set_calibration(_CALIBRATION)
    view.resize(1920, 1080)
    view._image_label.resize(1600, 1600)
    view._grid_checkbox.setChecked(True)
    view.on_frame(img)
    view.on_detection(detection)
    view._image_label.pixmap().save(str(out_path))


def _draw_shape_compare(img, detection, out_path: Path) -> None:
    """진단 전용 - 실제 화면이 아니라, ellipse(꼬리 포함)와 core_circle(코어만)을 확대해서
    비교. core_circle이 실제 원형 LED 광원 형상에 더 가깝다는 것을 보여주기 위함."""
    if detection.ellipse is None or detection.core_circle is None or detection.center_px is None:
        return
    cx, cy = detection.center_px
    (ecx, ecy), (major, minor), angle = detection.ellipse
    pad = int(max(major, minor) * 1.5) + 40
    x0, y0 = max(0, int(cx - pad)), max(0, int(cy - pad))
    x1, y1 = min(img.shape[1], int(cx + pad)), min(img.shape[0], int(cy + pad))
    crop = img[y0:y1, x0:x1].copy()

    ellipse_local = ((ecx - x0, ecy - y0), (major, minor), angle)
    cv2.ellipse(crop, ellipse_local, (0, 165, 255), 2)  # 주황 점선 느낌(꼬리 포함 - 기존 방식)

    (ccx, ccy), radius = detection.core_circle
    cv2.circle(crop, (int(round(ccx - x0)), int(round(ccy - y0))), int(round(radius)), (0, 255, 0), 2)  # 초록(신규 코어 원)
    cv2.drawMarker(crop, (int(round(cx - x0)), int(round(cy - y0))), (0, 0, 255),
                    markerType=cv2.MARKER_CROSS, markerSize=16, thickness=2)

    # 확대(작은 크롭이라 육안 확인이 어려움)
    scale = 4
    crop = cv2.resize(crop, (crop.shape[1] * scale, crop.shape[0] * scale), interpolation=cv2.INTER_NEAREST)
    cv2.putText(crop, "orange=ellipse(tail included)  green=core_circle(round)", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.imwrite(str(out_path), crop)


def main() -> None:
    global _CALIBRATION
    app = QApplication.instance() or QApplication(sys.argv)

    _CALIBRATION = _build_calibration()
    origin_x = _CALIBRATION.profile.origin_px_x
    origin_y = _CALIBRATION.profile.origin_px_y
    px_per_moa = _CALIBRATION.profile.px_per_moa_x
    print(f"[캘리브레이션] origin=({origin_x:.3f}, {origin_y:.3f}), px_per_moa={px_per_moa:.3f}")

    detector_settings = DetectionSettings()
    detector = RedDotDetector(detector_settings)
    tracker = BlobTracker(
        max_jump_px=200,
        elongation_correction_threshold=detector_settings.elongation_correction_threshold,
        elongation_correction_blend=detector_settings.elongation_correction_blend,
    )

    frame_files = sorted(VIDEO_DIR.glob("frame_*.jpg"))
    print(f"총 프레임 수: {len(frame_files)}")

    results = []
    frames_cache = {}
    detected_count = 0
    for f in frame_files:
        img = cv2.imread(str(f))
        detection = tracker.select(detector.detect(img))
        frames_cache[f.name] = (img, detection)
        entry = {"frame": f.name, "found": detection.found}
        if detection.found:
            detected_count += 1
            cx, cy = detection.center_px
            moa_x = (cx - origin_x) / px_per_moa
            moa_y = -(cy - origin_y) / px_per_moa
            major, minor = detection.ellipse[1] if detection.ellipse else (0.0, 0.0)
            elongation = (max(major, minor) / min(major, minor)) if min(major, minor) > 0 else None
            core_radius = detection.core_circle[1] if detection.core_circle else None
            entry.update({
                "center_px": [cx, cy],
                "moa": [round(moa_x, 3), round(moa_y, 3)],
                "ellipse_axes": [major, minor],
                "elongation": elongation,
                "core_circle_radius_px": core_radius,
            })
        results.append(entry)

    print(f"검출 성공: {detected_count}/{len(frame_files)}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    ranked_by_elongation = sorted(
        (r for r in results if r.get("elongation")),
        key=lambda r: -r["elongation"],
    )
    worst = [r["frame"].rsplit(".", 1)[0] for r in ranked_by_elongation[:3]]

    representative = {
        "start": results[0]["frame"].rsplit(".", 1)[0],
        "end": results[-1]["frame"].rsplit(".", 1)[0],
    }
    for i, name in enumerate(worst):
        representative[f"worst_elongation_{i + 1}"] = name

    for label, name in representative.items():
        img, detection = frames_cache[f"{name}.jpg"]
        out_path = RESULTS_DIR / f"{label}_{name}_liveview.png"
        _render_liveview(app, img, detection, out_path)
        print(f"저장: {out_path}")

    for name in worst:
        img, detection = frames_cache[f"{name}.jpg"]
        out_path = RESULTS_DIR / f"{name}_shape_compare.jpg"
        _draw_shape_compare(img, detection, out_path)
        print(f"저장: {out_path}")

    summary = {
        "calibration": {"origin_px": [origin_x, origin_y], "px_per_moa": px_per_moa},
        "total_frames": len(frame_files),
        "detected_count": detected_count,
        "representative_frames": representative,
        "results": results,
    }
    with open(RESULTS_DIR / "detection_summary.json", "w", encoding="utf-8") as fp:
        json.dump(summary, fp, ensure_ascii=False, indent=2)
    print(f"저장: {RESULTS_DIR / 'detection_summary.json'}")


if __name__ == "__main__":
    main()
