"""레드닷(조준경 조사점) 검출기.

- 순수 빨강이 아니라 호박색(amber)에 가까운 색상 (docs/detection_notes.md 참고).
- 중심 부근에서는 원형에 가깝지만 이동 범위 끝(예: 35MOA 부근)에서는 타원형으로 왜곡될 수 있음
  -> cv2.fitEllipse로 형상을 구하고, 중심점은 마스크의 무게중심(모멘트)으로 계산해 원형/타원형 모두
     동일하게 처리한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from core.config.settings import DetectionSettings


@dataclass
class DetectionResult:
    found: bool
    center_px: tuple[float, float] | None = None
    ellipse: tuple[tuple[float, float], tuple[float, float], float] | None = None  # (center,(w,h),angle)
    contour: np.ndarray | None = None
    area_px2: float = 0.0


class RedDotDetector:
    def __init__(self, settings: DetectionSettings | None = None) -> None:
        self.settings = settings or DetectionSettings()

    def detect(self, frame_bgr: np.ndarray) -> list[DetectionResult]:
        """프레임에서 후보 블롭을 모두 검출해 반환한다 (여러 개일 수 있음).

        여러 후보 중 실제 추적 대상을 고르는 것은 BlobTracker의 책임이다.
        """
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

        s = self.settings
        mask1 = cv2.inRange(hsv, np.array(s.hsv_lower1), np.array(s.hsv_upper1))
        mask2 = cv2.inRange(hsv, np.array(s.hsv_lower2), np.array(s.hsv_upper2))
        mask = cv2.bitwise_or(mask1, mask2)

        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        results: list[DetectionResult] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < s.min_blob_area:
                continue

            moments = cv2.moments(contour)
            if moments["m00"] == 0:
                continue
            cx = moments["m10"] / moments["m00"]
            cy = moments["m01"] / moments["m00"]

            ellipse = None
            if len(contour) >= 5:
                ellipse = cv2.fitEllipse(contour)

            results.append(
                DetectionResult(
                    found=True,
                    center_px=(cx, cy),
                    ellipse=ellipse,
                    contour=contour,
                    area_px2=area,
                )
            )

        results.sort(key=lambda r: r.area_px2, reverse=True)
        return results

    def detect_best(self, frame_bgr: np.ndarray) -> DetectionResult:
        """가장 큰 블롭 하나만 반환 (연속성 게이팅이 필요 없는 단순한 경우)."""
        candidates = self.detect(frame_bgr)
        if not candidates:
            return DetectionResult(found=False)
        return candidates[0]
