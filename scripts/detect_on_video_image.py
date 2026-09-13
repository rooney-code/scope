"""tests/test_images/video_image/ (100프레임)에 실제 프로덕션 검출 파이프라인
(RedDotDetector + BlobTracker)을 그대로 돌려 검출 성공률을 확인하고, 대표 프레임에
검출 결과(피팅된 타원 외곽선 + 중심점 마커)를 그려 저장한다.

이 폴더는 기존 조리개최대.jpg(캘리브레이션 기준 사진)와 동일 카메라로 촬영되었으므로,
`render_live_view_demo.py`에서 사용자가 최종 확정한 캘리브레이션 값(원점 nudge +2.216px,
px_per_moa 9.35)을 그대로 재사용한다 - 이 폴더만 다시 캘리브레이션하지 않는다.

실행: PYTHONPATH=. python3 scripts/detect_on_video_image.py
결과: tests/test_images/video_image_results/ (대표 프레임 오버레이 jpg + detection_summary.json)
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2

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


def _draw_overlay(img, detection):
    out = img.copy()
    if detection.ellipse is not None:
        cv2.ellipse(out, detection.ellipse, (0, 255, 0), 3)
    if detection.center_px is not None:
        cx, cy = detection.center_px
        cv2.drawMarker(out, (int(round(cx)), int(round(cy))), (0, 0, 255),
                        markerType=cv2.MARKER_CROSS, markerSize=30, thickness=3)
    return out


def main() -> None:
    bright = cv2.imread(str(ORIGINALS_DIR / "조리개최대.jpg"))
    grid_result = GridAutoDetector().detect(bright)
    if not grid_result.found:
        raise RuntimeError("그리드 원점 자동검출 실패 - 조리개최대.jpg를 확인하세요.")

    calibration = PixelAngleCalibration()
    calibration.seed_from_auto_detection("DEMO-CAM", grid_result)
    calibration.profile.px_per_moa_x = CONFIRMED_PX_PER_MOA
    calibration.profile.px_per_moa_y = CONFIRMED_PX_PER_MOA
    calibration.nudge_origin(dx_px=CONFIRMED_ORIGIN_NUDGE_PX)
    origin_x = calibration.profile.origin_px_x
    origin_y = calibration.profile.origin_px_y
    px_per_moa = calibration.profile.px_per_moa_x
    print(f"[캘리브레이션] origin=({origin_x:.3f}, {origin_y:.3f}), px_per_moa={px_per_moa:.3f}")

    detector = RedDotDetector(DetectionSettings())
    tracker = BlobTracker(max_jump_px=200)

    frame_files = sorted(VIDEO_DIR.glob("frame_*.jpg"))
    print(f"총 프레임 수: {len(frame_files)}")

    results = []
    detected_count = 0
    for f in frame_files:
        img = cv2.imread(str(f))
        detection = tracker.select(detector.detect(img))
        entry = {"frame": f.name, "found": detection.found}
        if detection.found:
            detected_count += 1
            cx, cy = detection.center_px
            moa_x = (cx - origin_x) / px_per_moa
            moa_y = -(cy - origin_y) / px_per_moa
            major, minor = detection.ellipse[1] if detection.ellipse else (0.0, 0.0)
            elongation = (max(major, minor) / min(major, minor)) if min(major, minor) > 0 else None
            entry.update({
                "center_px": [cx, cy],
                "moa": [round(moa_x, 3), round(moa_y, 3)],
                "ellipse_axes": [major, minor],
                "elongation": elongation,
            })
        results.append((entry, img, detection))

    print(f"검출 성공: {detected_count}/{len(frame_files)}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    ranked_by_elongation = sorted(
        (r for r in results if r[0].get("elongation")),
        key=lambda r: -r[0]["elongation"],
    )
    worst = [r[0]["frame"].rsplit(".", 1)[0] for r in ranked_by_elongation[:3]]

    representative = {
        "start": results[0][0]["frame"].rsplit(".", 1)[0],
        "end": results[-1][0]["frame"].rsplit(".", 1)[0],
    }
    for i, name in enumerate(worst):
        representative[f"worst_elongation_{i + 1}"] = name

    by_name = {r[0]["frame"].rsplit(".", 1)[0]: r for r in results}
    for label, name in representative.items():
        entry, img, detection = by_name[name]
        overlay = _draw_overlay(img, detection)
        out_path = RESULTS_DIR / f"{label}_{name}_detected.jpg"
        cv2.imwrite(str(out_path), overlay)
        print(f"저장: {out_path}")

    summary = {
        "calibration": {"origin_px": [origin_x, origin_y], "px_per_moa": px_per_moa},
        "total_frames": len(frame_files),
        "detected_count": detected_count,
        "representative_frames": representative,
        "results": [r[0] for r in results],
    }
    with open(RESULTS_DIR / "detection_summary.json", "w", encoding="utf-8") as fp:
        json.dump(summary, fp, ensure_ascii=False, indent=2)
    print(f"저장: {RESULTS_DIR / 'detection_summary.json'}")


if __name__ == "__main__":
    main()
