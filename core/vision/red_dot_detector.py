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
    # 바운딩박스를 짧은 변 크기의 정사각형들로 나눴을 때 가장 밝은 정사각형 - (center, radius
    # = 정사각형 한 변의 절반). 코멧테일 꼬리를 제외한 실제 원형 LED 광원의 형상을 나타낸다
    # (ellipse는 꼬리까지 포함해 늘어져 보일 수 있음). _fit_head_square() 참고.
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

            core_circle = self._fit_head_square(contour, mask, value_channel)

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
    def _fit_head_square(
        contour: np.ndarray, mask: np.ndarray, value_channel: np.ndarray
    ) -> tuple[tuple[float, float], float] | None:
        """윤곽선의 바운딩박스를 정사각형(한 변 = 짧은 변, 즉 늘어지지 않은 방향의 폭)들의
        연속으로 나눠보고, 그중 가장 밝은 정사각형을 "머리"(실제 원형 LED 광원)로 본다.

        코멧테일 꼬리는 늘어지는 방향으로만 길어지고 폭(짧은 변)은 거의 그대로 유지된다
        (실측으로 확인 - docs/detection_notes.md 10차 참고: 중심 부근 원형 폭 27px, 35MOA
        부근 늘어진 폭도 20px로 큰 차이 없음). 즉 짧은 변 길이가 곧 실제 LED 코어의 지름에
        해당하므로, 바운딩박스를 그 폭 크기의 정사각형들로 나누면 각 정사각형이 대략
        "그 위치의 단면"을 나타내고, 가장 밝은 정사각형이 꼬리가 아닌 머리다. 절대/상대 밝기
        임계값을 튜닝할 필요가 없는 순수 기하학적 방법(사용자 제안, 2026-09-13).
        """
        x, y, w, h = cv2.boundingRect(contour)
        if w <= 0 or h <= 0:
            return None

        side = min(w, h)
        if side <= 0:
            return None

        local_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.drawContours(local_mask, [contour], -1, 255, thickness=cv2.FILLED, offset=(-x, -y))
        local_mask = cv2.bitwise_and(local_mask, mask[y : y + h, x : x + w])

        vertical = h >= w
        length = h if vertical else w
        segment_count = max(1, round(length / side))

        best_center: tuple[float, float] | None = None
        best_brightness = -1.0
        for i in range(segment_count):
            lo = int(round(i * length / segment_count))
            hi = int(round((i + 1) * length / segment_count))
            if vertical:
                seg_mask = local_mask[lo:hi, :] > 0
                seg_value = value_channel[y + lo : y + hi, x : x + w]
                center = (x + w / 2.0, y + (lo + hi) / 2.0)
            else:
                seg_mask = local_mask[:, lo:hi] > 0
                seg_value = value_channel[y : y + h, x + lo : x + hi]
                center = (x + (lo + hi) / 2.0, y + h / 2.0)

            count = int(seg_mask.sum())
            if count == 0:
                continue
            avg_brightness = float(seg_value[seg_mask].astype(np.float64).sum()) / count
            if avg_brightness > best_brightness:
                best_brightness = avg_brightness
                best_center = center

        if best_center is None:
            return None
        return (best_center, side / 2.0)

    def detect_best(self, frame_bgr: np.ndarray) -> DetectionResult:
        """가장 큰 블롭 하나만 반환 (연속성 게이팅이 필요 없는 단순한 경우)."""
        candidates = self.detect(frame_bgr)
        if not candidates:
            return DetectionResult(found=False)
        return candidates[0]
