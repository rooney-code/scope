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
    # True면 이 결과가 정상 검출 범위(원점 기준 roi_margin_moa) 안이 아니라 전체 프레임
    # 폴백 검색으로 찾은 것 - 조립 상태에 따라 레드닷이 정상 이동 범위 밖에 있을 수 있어,
    # 사용자가 수동 조정으로 원점 쪽으로 가져오는 걸 돕기 위한 화면 표시 전용 결과다.
    # BlobTracker 연속성 추적이나 TravelTestStateMachine 이동량 판정에는 쓰면 안 된다
    # (InspectionViewModel._on_frame 참고, 사용자 요청 2026-09-15).
    out_of_range: bool = False


class RedDotDetector:
    def __init__(self, settings: DetectionSettings | None = None) -> None:
        self.settings = settings or DetectionSettings()

    def detect(
        self, frame_bgr: np.ndarray, roi_px: tuple[float, float, float, float] | None = None
    ) -> list[DetectionResult]:
        """프레임에서 후보 블롭을 모두 검출해 반환한다 (여러 개일 수 있음).

        여러 후보 중 실제 추적 대상을 고르는 것은 BlobTracker의 책임이다.

        roi_px: (x0, y0, x1, y1) 원본 프레임 좌표 기준 검출 영역 제한 - 캘리브레이션이
        확정되면 레드닷이 벗어날 수 없는 범위가 명확해지므로(호출부 참고), 그 영역
        밖은 아예 스캔하지 않는다. 반환되는 좌표/윤곽선은 모두 원본 프레임 좌표계로
        다시 변환되므로 호출부는 roi 유무를 신경 쓸 필요가 없다.
        """
        offset_x, offset_y = 0, 0
        search_frame = frame_bgr
        if roi_px is not None:
            fh, fw = frame_bgr.shape[:2]
            x0 = max(0, int(round(roi_px[0])))
            y0 = max(0, int(round(roi_px[1])))
            x1 = min(fw, int(round(roi_px[2])))
            y1 = min(fh, int(round(roi_px[3])))
            if x1 > x0 and y1 > y0:
                search_frame = frame_bgr[y0:y1, x0:x1]
                offset_x, offset_y = x0, y0

        hsv = cv2.cvtColor(search_frame, cv2.COLOR_BGR2HSV)

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
            if area < s.min_blob_area or area > s.max_blob_area:
                continue

            perimeter = cv2.arcLength(contour, True)
            circularity = (4 * np.pi * area / (perimeter**2)) if perimeter > 0 else 0.0
            if circularity < s.min_circularity:
                continue

            # 면적/원형도만으로 못 걸러낸 가짜 후보(문자/눈금 반사)를 피크 밝기로 추가 검증한다
            # - 실측으로 확인된 매우 뚜렷한 구분 기준(진짜 LED peak_v=255 vs 가짜 peak_v=2,
            # DetectionSettings.min_peak_brightness 참고). 이후 로직에서도 재사용하므로 먼저
            # 계산해둔다.
            bx, by, bw, bh = cv2.boundingRect(contour)
            local_mask = np.zeros((bh, bw), dtype=np.uint8)
            cv2.drawContours(local_mask, [contour], -1, 255, thickness=cv2.FILLED, offset=(-bx, -by))
            local_mask = cv2.bitwise_and(local_mask, mask[by : by + bh, bx : bx + bw])
            seg_v = value_channel[by : by + bh, bx : bx + bw]
            peak_v = int(seg_v[local_mask > 0].max()) if (local_mask > 0).any() else 0
            if peak_v < s.min_peak_brightness:
                continue

            moments = cv2.moments(contour)
            if moments["m00"] == 0:
                continue

            ellipse = None
            if len(contour) >= 5:
                ellipse = cv2.fitEllipse(contour)

            core_circle = self._fit_head_square(contour, mask, value_channel)
            if core_circle is not None:
                # 정사각형 분할로 찾은 "머리"의 중심을 그대로 검출 중심으로 사용 - 코멧테일
                # 꼬리를 포함해 쏠리는 문제가 없는 기하학적 방법이므로, 픽셀 단위 밝기
                # 가중치(_intensity_weighted_centroid)보다 우선한다. 사용자 확인 사항
                # (2026-09-13), docs/detection_notes.md 14차 참고.
                center = core_circle[0]
            else:
                center = self._intensity_weighted_centroid(contour, mask, value_channel, s.centroid_intensity_power)
                if center is None:
                    center = (moments["m10"] / moments["m00"], moments["m01"] / moments["m00"])

            # roi_px로 잘라낸 영역 안에서 계산했으므로, 반환 직전에 원본 프레임 좌표계로
            # 되돌린다 - 호출부(BlobTracker/캘리브레이션 등)는 항상 원본 좌표만 다루면 된다.
            if offset_x or offset_y:
                center = (center[0] + offset_x, center[1] + offset_y)
                contour = contour + (offset_x, offset_y)
                if ellipse is not None:
                    (ecx, ecy), esize, eangle = ellipse
                    ellipse = ((ecx + offset_x, ecy + offset_y), esize, eangle)
                if core_circle is not None:
                    (ccx, ccy), cradius = core_circle
                    core_circle = ((ccx + offset_x, ccy + offset_y), cradius)

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

        단, 세로/가로 비율이 MIN_ELONGATION_RATIO(2.0) 미만이면(거의 원형에 가까움)
        아예 적용하지 않고 None을 반환한다(호출부가 무게중심 계산으로 폴백) - 살짝만
        길쭉한 거의-원형 블롭은 밝기가 두 조각 사이에 엇비슷해서, 조각을 나눠 비교하는
        방식 자체가 노이즈에 취약해 오히려 안정적인 무게중심보다 못한(때로는 꼬리 쪽을
        "머리"로 잘못 고르는) 결과를 내는 문제가 실측으로 확인됐다(2026-09-14: 세로/가로
        36:24=1.5:1인 블롭에서 무게중심 y=447.3인데 이 로직은 y=457.0을 골라 10px 이상
        어긋남). 뚜렷하게 길쭉한 진짜 코멧테일에서만 이 로직을 쓰도록 제한한다.
        """
        x, y, w, h = cv2.boundingRect(contour)
        if w <= 0 or h <= 0:
            return None

        MIN_ELONGATION_RATIO = 2.0
        if max(w, h) / min(w, h) < MIN_ELONGATION_RATIO:
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

        segments: list[tuple[float, tuple[float, float]]] = []  # (avg_brightness, center)
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
            segments.append((avg_brightness, center))

        if not segments:
            return None

        # 밝기가 거의 동일한(대략 균일한 밝기의 정상적인 원형/타원형 - 코멧테일이 아닌 경우)
        # 구간들 사이에서는 특정 구간을 임의로(예: 첫 구간) 고르지 않고, 동률인 구간들의
        # 중심을 평균해 대칭적인 결과가 나오게 한다(구간 수가 짝수라 정중앙 구간이 없어도
        # 대칭 위치를 유지). 코멧테일처럼 한 구간이 뚜렷이 밝을 때만 그 구간 하나만 남아
        # 그대로 선택된다.
        max_brightness = max(b for b, _ in segments)
        tolerance = max_brightness * 0.02
        winners = [c for b, c in segments if b >= max_brightness - tolerance]
        losers_brightness = [b for b, _ in segments if b < max_brightness - tolerance]

        # 세로/가로 비율 기준(호출부)만으로는 부족하다 - 뚜렷하게 길쭉해도 조각 간 밝기
        # 차이가 애매하면(노이즈 수준) 여전히 엉뚱한 조각을 "머리"로 고를 수 있다(사용자
        # 지적, 2026-09-14: 크기 기준만 바꾸면 우연히 또 잘못된 조각을 고르는 경우가
        # 재발할 수 있음). 남은(밝지 않은) 조각들의 평균보다 승자가 충분히(15% 이상)
        # 밝을 때만 신뢰하고, 그 정도 차이가 안 나면 확신이 부족하다고 보고 None을
        # 반환해 호출부가 무게중심으로 폴백하게 한다.
        MIN_HEAD_CONTRAST_RATIO = 0.15
        if losers_brightness:
            losers_avg = sum(losers_brightness) / len(losers_brightness)
            if losers_avg <= 0 or max_brightness < losers_avg * (1 + MIN_HEAD_CONTRAST_RATIO):
                return None

        best_center = (
            sum(c[0] for c in winners) / len(winners),
            sum(c[1] for c in winners) / len(winners),
        )
        return (best_center, side / 2.0)

    def detect_best(self, frame_bgr: np.ndarray) -> DetectionResult:
        """가장 큰 블롭 하나만 반환 (연속성 게이팅이 필요 없는 단순한 경우)."""
        candidates = self.detect(frame_bgr)
        if not candidates:
            return DetectionResult(found=False)
        return candidates[0]
