"""레드닷(조준경 조사점) 검출기.

- 순수 빨강이 아니라 호박색(amber)에 가까운 색상 (docs/detection_notes.md 참고).
- 중심 부근에서는 원형에 가깝지만 이동 범위 끝(예: 35MOA 부근)에서는 단순 타원형이 아니라
  "코멧테일"(밝은 머리 + 중심 반대쪽으로 흐려지는 꼬리) 형태로 왜곡될 수 있음 - 레티클과
  반사렌즈 간 거리/발산각이 달라지는 제품 구조상의 광학 특성(실측 영상으로 고객이 확인,
  docs/detection_notes.md 참고). 이진 마스크의 단순 무게중심(모든 픽셀 동일 가중치)은 꼬리
  쪽으로 중심이 쏠리므로, 밝기로 가중치를 준 무게중심을 사용해 밝은 "머리" 부분이 중심 계산을
  지배하도록 한다(_intensity_weighted_centroid).
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
    # 밝기 상위 영역("코어")만의 최소외접원 - (center, radius). 코멧테일 꼬리를 제외한
    # 실제 원형 LED 광원의 형상을 나타낸다(ellipse는 꼬리까지 포함해 늘어져 보일 수 있음).
    core_circle: tuple[tuple[float, float], float] | None = None
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

        value_channel = hsv[..., 2]
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        results: list[DetectionResult] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < s.min_blob_area:
                continue

            moments = cv2.moments(contour)
            if moments["m00"] == 0:
                continue

            center = self._intensity_weighted_centroid(contour, mask, value_channel, s.centroid_intensity_power)
            if center is None:
                center = (moments["m10"] / moments["m00"], moments["m01"] / moments["m00"])

            ellipse = None
            if len(contour) >= 5:
                ellipse = cv2.fitEllipse(contour)

            core_circle = self._fit_core_circle(contour, mask, value_channel, s.core_brightness_ratio)

            results.append(
                DetectionResult(
                    found=True,
                    center_px=center,
                    ellipse=ellipse,
                    core_circle=core_circle,
                    contour=contour,
                    area_px2=area,
                )
            )

        results.sort(key=lambda r: r.area_px2, reverse=True)
        return results

    @staticmethod
    def _intensity_weighted_centroid(
        contour: np.ndarray, mask: np.ndarray, value_channel: np.ndarray, power: float
    ) -> tuple[float, float] | None:
        """윤곽선 영역 내부만 밝기(V채널)로 가중치를 준 무게중심 - "코멧테일" 꼬리(어두움)보다
        밝은 머리 쪽에 더 큰 가중치를 줘서 중심이 꼬리로 쏠리는 것을 억제한다. power<=0이면
        가중치 없이(균등) 계산해 기존 이진 무게중심과 동일해진다."""
        x, y, w, h = cv2.boundingRect(contour)
        if w <= 0 or h <= 0:
            return None

        local_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.drawContours(local_mask, [contour], -1, 255, thickness=cv2.FILLED, offset=(-x, -y))
        local_mask = cv2.bitwise_and(local_mask, mask[y : y + h, x : x + w])

        weights = (local_mask > 0).astype(np.float64)
        if power > 0:
            brightness = value_channel[y : y + h, x : x + w].astype(np.float64) / 255.0
            weights *= np.power(brightness, power)

        total = weights.sum()
        if total <= 0:
            return None

        ys, xs = np.mgrid[0:h, 0:w]
        cx = float((xs * weights).sum() / total) + x
        cy = float((ys * weights).sum() / total) + y
        return (cx, cy)

    @staticmethod
    def _fit_core_circle(
        contour: np.ndarray, mask: np.ndarray, value_channel: np.ndarray, ratio: float
    ) -> tuple[tuple[float, float], float] | None:
        """윤곽선 내부에서 밝기 상위(코어) 영역만 최소외접원으로 감싼다.

        코멧테일 꼬리는 어둡지만 여전히 HSV 임계값을 넘어 윤곽선(및 fitEllipse)에 포함되므로,
        형상만 보면 늘어진 타원으로 보인다. 하지만 실제 레드닷(LED)은 항상 원형이고 꼬리는
        광학적 산란일 뿐이므로, 블롭 내부의 (최소~최대) 밝기 범위에서 상위 `ratio` 구간에
        속하는 픽셀("코어")만으로 원을 피팅해 실제 형상에 맞는 원형 결과를 낸다. 절대 밝기
        (예: 최대값의 X%) 대신 블롭 내 상대 범위를 쓰는 이유는 settings.py의
        core_brightness_ratio 주석 참고.
        """
        x, y, w, h = cv2.boundingRect(contour)
        if w <= 0 or h <= 0:
            return None

        local_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.drawContours(local_mask, [contour], -1, 255, thickness=cv2.FILLED, offset=(-x, -y))
        local_mask = cv2.bitwise_and(local_mask, mask[y : y + h, x : x + w])

        brightness = value_channel[y : y + h, x : x + w]
        blob_brightness = brightness[local_mask > 0]
        if blob_brightness.size == 0:
            return None

        b_min, b_max = float(blob_brightness.min()), float(blob_brightness.max())
        threshold = b_min + ratio * (b_max - b_min)
        core_mask = np.where((local_mask > 0) & (brightness >= threshold), 255, 0).astype(np.uint8)
        points = cv2.findNonZero(core_mask)
        if points is None:
            return None

        (cx, cy), radius = cv2.minEnclosingCircle(points)
        return ((cx + x, cy + y), radius)

    def detect_best(self, frame_bgr: np.ndarray) -> DetectionResult:
        """가장 큰 블롭 하나만 반환 (연속성 게이팅이 필요 없는 단순한 경우)."""
        candidates = self.detect(frame_bgr)
        if not candidates:
            return DetectionResult(found=False)
        return candidates[0]
