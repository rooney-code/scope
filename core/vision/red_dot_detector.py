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
        self,
        frame_bgr: np.ndarray,
        roi_px: tuple[float, float, float, float] | None = None,
        head_direction_hint_px: tuple[float, float] | None = None,
    ) -> list[DetectionResult]:
        """프레임에서 후보 블롭을 모두 검출해 반환한다 (여러 개일 수 있음).

        여러 후보 중 실제 추적 대상을 고르는 것은 BlobTracker의 책임이다.

        roi_px: (x0, y0, x1, y1) 원본 프레임 좌표 기준 검출 영역 제한 - 캘리브레이션이
        확정되면 레드닷이 벗어날 수 없는 범위가 명확해지므로(호출부 참고), 그 영역
        밖은 아예 스캔하지 않는다. 반환되는 좌표/윤곽선은 모두 원본 프레임 좌표계로
        다시 변환되므로 호출부는 roi 유무를 신경 쓸 필요가 없다.

        head_direction_hint_px: (dx, dy) 방향 힌트(부호만 씀) - 코멧테일인데 머리/꼬리
        밝기 차이가 애매한 경우(_fit_head_square의 MIN_HEAD_CONTRAST_RATIO 미달) 무게
        중심 대신 이 방향으로 살짝 옮긴 추정치를 쓴다. 보통 현재 시험 중인 방향(상/하/
        좌/우)을 호출부(InspectionViewModel)가 넘겨준다 - 그 방향으로 레드닷이 멀어지며
        늘어지는 것이므로 "머리"도 그쪽에 있다고 본다(사용자 제안, 2026-09-15).
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
                if s.debug_logging:
                    reason = "너무 작음" if area < s.min_blob_area else "너무 큼"
                    print(
                        f"[detect] 제외({reason}): area={area:.0f} "
                        f"(허용 {s.min_blob_area:.0f}~{s.max_blob_area:.0f})"
                    )
                continue

            perimeter = cv2.arcLength(contour, True)
            circularity = (4 * np.pi * area / (perimeter**2)) if perimeter > 0 else 0.0
            if circularity < s.min_circularity:
                if s.debug_logging:
                    print(
                        f"[detect] 제외(비원형): area={area:.0f} circularity={circularity:.3f} "
                        f"(최소 {s.min_circularity:.2f})"
                    )
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
                if s.debug_logging:
                    print(
                        f"[detect] 제외(어두움): area={area:.0f} circularity={circularity:.3f} "
                        f"peak_v={peak_v} (최소 {s.min_peak_brightness})"
                    )
                continue

            moments = cv2.moments(contour)
            if moments["m00"] == 0:
                continue

            ellipse = None
            if len(contour) >= 5:
                ellipse = cv2.fitEllipse(contour)

            core_circle = self._fit_head_square(
                contour, mask, value_channel, head_direction_hint_px, s.debug_logging, (offset_x, offset_y)
            )
            if s.debug_logging:
                elongation = max(bw, bh) / min(bw, bh) if min(bw, bh) > 0 else 0.0
                center_method = "head_square" if core_circle is not None else "centroid"
                print(
                    f"[detect] 형상: bbox=({bw}x{bh}) elongation={elongation:.2f} "
                    f"center_method={center_method}"
                )
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

            # 실측 이미지에서 "검출된 중심이 실제 가장 밝은 부분보다 오른쪽 아래로 쏠려
            # 보인다"는 지적(2026-09-16) - 과포화(peak_v=255)된 픽셀은 power를 아무리
            # 올려도 가중치가 항상 1.0이라(1.0**power == 1.0) 서로 구분이 안 되고, 결국
            # 어두운 halo까지 끌어들인 전체 영역의 무게중심에 가까워지는 게 아닌가 하는
            # 가설을 세웠다. 실제로 적용하기 전에 대안(power를 더 올리기 / 마스크 자체를
            # 밝은 픽셀로 좁히기)이 중심을 어디로 옮기는지 로그로만 먼저 추정해본다 - center
            # 자체는 그대로 두고 비교용 출력만 남긴다(사용자 요청, 2026-09-16).
            if s.debug_logging and core_circle is None:
                current_display = (center[0] + offset_x, center[1] + offset_y)
                estimates = self._debug_centroid_estimates(contour, mask, value_channel, (offset_x, offset_y))
                print(
                    f"[detect] 중심 추정 비교(현재 power={s.centroid_intensity_power:.1f}: "
                    f"{current_display[0]:.1f},{current_display[1]:.1f}): {estimates}"
                )

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

            if s.debug_logging:
                print(
                    f"[detect] 후보: area={area:.0f} circularity={circularity:.3f} "
                    f"peak_v={peak_v} center=({center[0]:.1f},{center[1]:.1f})"
                )

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

        if s.debug_logging and not results:
            where = f"ROI({roi_px[0]:.0f},{roi_px[1]:.0f})~({roi_px[2]:.0f},{roi_px[3]:.0f})" if roi_px else "전체 프레임"
            print(f"[detect] 후보 없음 - {where} 안에서 조건을 통과한 윤곽선이 하나도 없음")

        results.sort(key=lambda r: r.area_px2, reverse=True)
        return results

    @staticmethod
    def _debug_centroid_estimates(
        contour: np.ndarray,
        mask: np.ndarray,
        value_channel: np.ndarray,
        offset_px: tuple[float, float],
    ) -> str:
        """실제 검출에는 전혀 관여하지 않는 순수 진단용 - 검출된 중심이 실제 가장 밝은
        부분(육안)보다 어두운 halo 쪽으로 쏠려 보인다는 지적(2026-09-16)에 따라, 코드를
        실제로 바꾸기 전에 두 가지 대안이 중심을 어디로 옮기는지 로그로 먼저 추정한다:
        (1) 밝기 가중치(power)를 지금보다 훨씬 세게(6.0/10.0) 주면 어떻게 되는지 - 단,
        과포화(V=255) 픽셀은 1.0**power == 1.0이라 power를 아무리 올려도 서로 구분되지
        않고 동일하게 최대 가중치를 유지하므로, "이미 과포화된 넓은 영역" 자체의 무게중심
        에서 크게 못 벗어날 수 있다. (2) 마스크 자체를 V>=250인 픽셀만으로 좁혀서(halo를
        아예 제외) 그 좁은 핵심 영역만의 무게중심을 구하면 어떻게 되는지 - 이쪽이 halo의
        영향을 원천 차단하므로 이론상 더 효과적일 가능성이 높다."""
        x, y, w, h = cv2.boundingRect(contour)
        if w <= 0 or h <= 0:
            return "n/a"

        local_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.drawContours(local_mask, [contour], -1, 255, thickness=cv2.FILLED, offset=(-x, -y))
        local_mask = cv2.bitwise_and(local_mask, mask[y : y + h, x : x + w])
        value_crop = value_channel[y : y + h, x : x + w].astype(np.float64)
        ox, oy = offset_px

        def _weighted_center(mask_arr: np.ndarray, power: float) -> str:
            weights = (mask_arr > 0).astype(np.float64)
            if power > 0:
                weights *= np.power(value_crop / 255.0, power)
            total = weights.sum()
            if total <= 0:
                return "n/a"
            ys, xs = np.mgrid[0:h, 0:w]
            cx = float((xs * weights).sum() / total) + x + ox
            cy = float((ys * weights).sum() / total) + y + oy
            return f"({cx:.1f},{cy:.1f})"

        # 옵션2: halo(어둡고 넓게 퍼진 부분)를 아예 마스크에서 빼고 거의 포화된 핵심
        # 픽셀(V>=250)만 남긴다 - 이 부분마저 비어있으면(과포화 영역이 거의 없으면)
        # "n/a"로 표시된다.
        strict_mask = np.where(value_crop >= 250, local_mask, 0).astype(np.uint8)

        return (
            f"power=6.0:{_weighted_center(local_mask, 6.0)} "
            f"power=10.0:{_weighted_center(local_mask, 10.0)} "
            f"strict_v>=250(power=2.0):{_weighted_center(strict_mask, 2.0)}"
        )

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
        contour: np.ndarray,
        mask: np.ndarray,
        value_channel: np.ndarray,
        head_direction_hint_px: tuple[float, float] | None = None,
        debug_logging: bool = False,
        debug_offset_px: tuple[float, float] = (0.0, 0.0),
    ) -> tuple[tuple[float, float], float] | None:
        """윤곽선의 바운딩박스를 짧은 변(늘어지지 않은 방향의 폭) 지름의 원으로, 긴 축을
        따라 겹치게(슬라이딩 윈도우) 훑으면서 평균 밝기가 가장 높은 위치를 "머리"(실제
        원형 LED 광원)로 본다.

        코멧테일 꼬리는 늘어지는 방향으로만 길어지고 폭(짧은 변)은 거의 그대로 유지된다
        (실측으로 확인 - docs/detection_notes.md 10차 참고: 중심 부근 원형 폭 27px, 35MOA
        부근 늘어진 폭도 20px로 큰 차이 없음). 즉 짧은 변 길이가 곧 실제 LED 코어의 지름에
        해당하므로, 그 지름의 원으로 단면을 재면 가장 밝은 위치가 꼬리가 아닌 머리다.
        절대/상대 밝기 임계값을 튜닝할 필요가 없는 순수 기하학적 방법(사용자 제안,
        2026-09-13). 처음엔 원이 아니라 정사각형 블록으로 겹치지 않게 등분했었는데,
        실제 원형 광원이 두 블록 경계에 걸치면 어느 블록도 그 밝기를 온전히 못 담아
        밝기 차이가 실제보다 작게 측정되는(그래서 머리/꼬리 확신을 못 갖는 경우가
        잦아지는) 문제가 있었다(사용자 지적, 2026-09-15) - 원형 슬라이딩 윈도우는 경계
        위치와 무관하게 항상 같은 모양으로 광원을 담아 이 손실이 줄어든다.

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
        radius_px = side / 2.0

        # 긴 축을 따라 원형 창을 겹치게(step을 반지름의 1/2 정도로 촘촘히) 슬라이딩한다 -
        # 창 하나하나가 정확히 "그 위치의 단면"이 되도록 반지름은 항상 radius_px로 고정.
        # 창이 bbox 경계에서 잘리는 위치([0, radius_px)와 (length-radius_px, length])는
        # 제외한다 - 잘린 창은 실제 면적의 절반도 안 되는데도 평균 밝기 계산에는 그
        # 사실이 반영되지 않아(평균은 표본 개수와 무관), 머리 내부에 완전히 들어가는
        # 정상 창과 "우연히 똑같이 순수 최댓값"으로 동률 처리되는 문제가 있었다(실측으로
        # 확인, 2026-09-15: 진짜 중심 x=200인데 경계에서 잘린 창들까지 동률 평균에 끼어
        # x=196 근처로 쏠림). MIN_ELONGATION_RATIO(2.0) 덕분에 length >= 4*radius_px가
        # 보장되므로 [radius_px, length-radius_px] 구간은 항상 폭 2*radius_px 이상으로
        # 비어있지 않다.
        step = max(1.0, radius_px / 2.0)
        lo_bound, hi_bound = radius_px, length - radius_px
        positions = [p for p in np.arange(lo_bound, hi_bound, step)]
        if not positions or positions[-1] != hi_bound:
            positions.append(hi_bound)

        ys, xs = np.mgrid[0:h, 0:w]
        segments: list[tuple[float, tuple[float, float]]] = []  # (avg_brightness, center)
        for p in positions:
            if vertical:
                cx_local, cy_local = w / 2.0, p
            else:
                cx_local, cy_local = p, h / 2.0

            window_mask = ((xs - cx_local) ** 2 + (ys - cy_local) ** 2 <= radius_px**2) & (local_mask > 0)
            count = int(window_mask.sum())
            if count == 0:
                continue
            avg_brightness = float(value_channel[y : y + h, x : x + w][window_mask].astype(np.float64).sum()) / count
            segments.append((avg_brightness, (x + cx_local, y + cy_local)))

        if not segments:
            return None

        # 예전(정사각형을 겹치지 않게 몇 개 안 되는 블록으로 등분)에는 대칭인 모양이면
        # 두 블록이 거의 동률이 되어, 그 동률 블록들의 중심을 평균해야 대칭 위치가
        # 나왔다. 슬라이딩 원(연속적으로 촘촘히 겹쳐 스캔)에서는 대칭인 모양이면 스캔
        # 자체가 이미 정중앙에서 정점을 찍으므로 평균이 필요 없다 - 오히려 정점 부근의
        # 완만하게 퍼지는 곡선(코멧테일처럼 한쪽으로만 서서히 어두워지는 경우)에서 정점
        # 근처의 비대칭적으로 많은 샘플까지 평균에 끼면 다시 꼬리 쪽으로 쏠리는 회귀가
        # 실측으로 확인됐다(2026-09-15) - 부동소수점 오차 수준의 완전한 동률에서만
        # 평균내고, 그 외에는 정점 위치를 그대로 쓴다.
        max_brightness = max(b for b, _ in segments)
        tolerance = max_brightness * 1e-6
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
                # 밝기로 머리/꼬리를 구분할 수 없는 경우(예: 타원 전체가 고르게 밝음 -
                # 실측으로 확인, 2026-09-15) - 한때 진행방향 힌트로 반지름만큼 옮기는
                # 보정을 시도했으나, 일단 빼기로 함(사용자 요청, 2026-09-15 - 슬라이딩
                # 원 방식 자체의 정확도를 먼저 방향 힌트 없이 검증한 뒤 다시 판단하기로
                # 함). head_direction_hint_px 파라미터/전달 경로는 나중에 다시 켤 수
                # 있도록 그대로 남겨둔다.
                if debug_logging:
                    print("[detect] head_square: 밝기 대비 부족 -> 무게중심 폴백")
                return None

        if debug_logging:
            print("[detect] head_square: 밝기 대비로 머리 확정(신뢰도 높음)")
        best_center = (
            sum(c[0] for c in winners) / len(winners),
            sum(c[1] for c in winners) / len(winners),
        )
        return (best_center, side / 2.0)

    def detect_best(
        self, frame_bgr: np.ndarray, head_direction_hint_px: tuple[float, float] | None = None
    ) -> DetectionResult:
        """가장 큰 블롭 하나만 반환 (연속성 게이팅이 필요 없는 단순한 경우)."""
        candidates = self.detect(frame_bgr, head_direction_hint_px=head_direction_hint_px)
        if not candidates:
            return DetectionResult(found=False)
        return candidates[0]
