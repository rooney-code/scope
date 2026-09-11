"""보어사이터 그리드(십자선 + tick)의 자동 검출.

Hough Line Transform으로 십자선(긴 수평/수직 직선)을 찾아 교차점(원점 후보)을 구하고,
십자선을 따라 짧은 tick들의 위치(px)를 추정한다.

기본 파라미터(Canny/Hough 임계값)는 실제 현장 캡처 이미지(3088x2076, 조리개 최대 상태)로
검증/튜닝한 값이다 - 초기 기본값은 합성 테스트 이미지 기준으로 너무 엄격해서 실제 사진에서는
십자선을 전혀 못 찾는 문제가 있었음(대비가 낮고 선이 짧은 구간으로 끊겨 보임).

정확도는 완벽하지 않을 수 있으며(조명/각도/텍스트 라벨 간섭), 계획서에 따라 이 결과는
캘리브레이션 UI에서 작업자의 클릭 스냅/화살표 미세조정으로 보정하는 것을 전제로 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class GridDetectionResult:
    found: bool
    origin_px: tuple[float, float] | None = None
    horizontal_angle_deg: float = 0.0  # 0 = 완전 수평
    vertical_angle_deg: float = 0.0  # 90 = 완전 수직
    tick_x_positions_px: list[float] = field(default_factory=list)  # 원점 기준 상대값 아님, 절대 x 좌표
    tick_y_positions_px: list[float] = field(default_factory=list)


class GridAutoDetector:
    def __init__(
        self,
        hough_threshold: int = 40,
        min_line_length_ratio: float = 0.1,  # 프레임 폭/높이 대비 최소 직선 길이 비율
        max_line_gap: int = 40,
        angle_tolerance_deg: float = 3.0,
        canny_threshold1: int = 10,
        canny_threshold2: int = 50,
    ) -> None:
        self.hough_threshold = hough_threshold
        self.min_line_length_ratio = min_line_length_ratio
        self.max_line_gap = max_line_gap
        self.angle_tolerance_deg = angle_tolerance_deg
        self.canny_threshold1 = canny_threshold1
        self.canny_threshold2 = canny_threshold2

    def detect(self, frame_bgr: np.ndarray) -> GridDetectionResult:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]

        edges = cv2.Canny(gray, self.canny_threshold1, self.canny_threshold2)
        min_len = int(min(w, h) * self.min_line_length_ratio)
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=self.hough_threshold,
            minLineLength=min_len,
            maxLineGap=self.max_line_gap,
        )
        if lines is None:
            return GridDetectionResult(found=False)

        horizontals = []
        verticals = []
        for line in lines:
            # cv2 버전에 따라 HoughLinesP 반환 shape이 (N,1,4) 또는 (N,4)로 다를 수 있음
            coords = line[0] if line.ndim > 1 else line
            x1, y1, x2, y2 = coords
            angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            length = np.hypot(x2 - x1, y2 - y1)
            if abs(angle) <= self.angle_tolerance_deg:
                horizontals.append((length, x1, y1, x2, y2, angle))
            elif abs(abs(angle) - 90) <= self.angle_tolerance_deg:
                verticals.append((length, x1, y1, x2, y2, angle))

        if not horizontals or not verticals:
            return GridDetectionResult(found=False)

        # 1단계: Hough 선분들로 대략적인 위치/기울기를 구한다 (탐색 시작점 용도).
        h_slope, h_intercept = self._fit_line(horizontals)  # y = h_slope * x + h_intercept
        v_slope, v_intercept = self._fit_line(verticals)  # x = v_slope * y + v_intercept

        denom = 1 - h_slope * v_slope
        if abs(denom) < 1e-9:
            return GridDetectionResult(found=False)
        approx_origin_x = (v_slope * h_intercept + v_intercept) / denom
        approx_origin_y = h_slope * approx_origin_x + h_intercept

        # 2단계: Hough 선분의 끝점 좌표는 근본적으로 정수 픽셀 단위라 오차가 크다(실측 결과
        # 원본 사진에서 4~5px, 약 0.5MOA 수준의 오차가 났음). 근처 밝기(어두운 정도)의
        # 가중 무게중심을 촘촘히 여러 지점에서 측정해 서브픽셀 단위로 각 축의 위치를 다시
        # 잡고, 그 점들로 재차 직선을 피팅해 정밀한 교차점을 구한다.
        v_slope2, v_intercept2 = self._refine_axis_line(
            gray, axis="vertical", approx_coord=approx_origin_x, scan_start=50, scan_end=h - 50
        )
        h_slope2, h_intercept2 = self._refine_axis_line(
            gray, axis="horizontal", approx_coord=approx_origin_y, scan_start=50, scan_end=w - 50
        )

        if v_slope2 is not None and h_slope2 is not None:
            v_slope, v_intercept = v_slope2, v_intercept2
            h_slope, h_intercept = h_slope2, h_intercept2
            denom = 1 - h_slope * v_slope
            if abs(denom) < 1e-9:
                return GridDetectionResult(found=False)

        origin_x = (v_slope * h_intercept + v_intercept) / denom
        origin_y = h_slope * origin_x + h_intercept

        h_angle = float(np.degrees(np.arctan(h_slope)))
        v_angle = float(np.degrees(np.arctan(v_slope)) + 90.0 if v_slope >= 0 else np.degrees(np.arctan(v_slope)) - 90.0)

        tick_x = self._detect_ticks(gray, axis="x", axis_coord=origin_y)
        tick_y = self._detect_ticks(gray, axis="y", axis_coord=origin_x)

        return GridDetectionResult(
            found=True,
            origin_px=(origin_x, origin_y),
            horizontal_angle_deg=h_angle,
            vertical_angle_deg=v_angle,
            tick_x_positions_px=tick_x,
            tick_y_positions_px=tick_y,
        )

    @staticmethod
    def _fit_line(candidates: list[tuple]) -> tuple[float, float]:
        """후보 선분들(가장 긴 것 + 그 근방 ±8px 이내의 조각들)의 양 끝점을 모두 모아
        최소자승으로 직선을 피팅한다. 수평선은 y=f(x), 수직선은 x=f(y) 형태로 피팅해
        수직선이 90도에 가깝더라도(기울기 무한대 문제 없이) 안정적으로 처리한다.

        candidates 튜플 형식: (length, x1, y1, x2, y2, angle) - x1,y1,x2,y2가 두 끝점.
        같은 x1,y1,x2,y2,angle 필드를 수평/수직 후보 모두에 공통으로 사용하되, 어느 축을
        기준으로 피팅할지는 angle로 판단(수평 후보는 45도 미만, 수직 후보는 45도 이상).
        """
        candidates_sorted = sorted(candidates, key=lambda t: t[0], reverse=True)
        longest = candidates_sorted[0]
        is_horizontal = abs(longest[5]) < 45

        ref_pos = (
            (longest[2] + longest[4]) / 2.0 if is_horizontal else (longest[1] + longest[3]) / 2.0
        )
        nearby = [
            c
            for c in candidates_sorted
            if abs(((c[2] + c[4]) / 2.0 if is_horizontal else (c[1] + c[3]) / 2.0) - ref_pos) <= 8
        ]

        xs: list[float] = []
        ys: list[float] = []
        for _, x1, y1, x2, y2, _ in nearby:
            xs.extend([x1, x2])
            ys.extend([y1, y2])

        if is_horizontal:
            slope, intercept = np.polyfit(xs, ys, 1)  # y = slope*x + intercept
        else:
            slope, intercept = np.polyfit(ys, xs, 1)  # x = slope*y + intercept
        return float(slope), float(intercept)

    @classmethod
    def _refine_axis_line(
        cls,
        gray: np.ndarray,
        axis: str,
        approx_coord: float,
        scan_start: int,
        scan_end: int,
        search_radius: int = 10,
        step: int = 5,
        iterations: int = 3,
    ) -> tuple[float | None, float | None]:
        """대략적인 축 위치(approx_coord) 근방을 촘촘히 스캔하며, 각 스캔 라인에서 밝기의
        가중 무게중심(어두울수록 가중치 높음)으로 축 선의 서브픽셀 위치를 구한 뒤, 그 점들로
        다시 직선을 피팅한다. Hough 선분 끝점(정수 픽셀)보다 훨씬 정밀하다.

        두 가지를 추가로 처리한다(실측 검증으로 필요성이 확인됨):
        - **배경 기울기 제거(detrend)**: 사진에 비네팅(가장자리로 갈수록 어두워짐)이 있어
          탐색 구간 양 끝의 밝기가 다르면 무게중심이 실제 선 위치에서 밀리는 문제가 있었음 -
          각 스캔 라인에서 양 끝 몇 개 값으로 선형 배경을 추정해 제거한 뒤 무게중심을 구함.
        - **반복(iteration)**: 첫 근사 위치가 실제 위치에서 조금 떨어져 있으면 탐색창이
          비대칭적으로 배경을 포함해 결과가 근사 위치에 따라 흔들렸음 - 구한 위치를 다음
          반복의 중심으로 다시 사용해 수렴시킨다(보통 2~3회면 충분).

        axis="vertical": 여러 y에서 x를 찾아 x = slope*y + intercept 반환.
        axis="horizontal": 여러 x에서 y를 찾아 y = slope*x + intercept 반환.
        실패 시 (None, None).
        """
        coord = approx_coord
        slope, intercept = None, None

        for _ in range(iterations):
            primary: list[float] = []  # scan 좌표 (y for vertical, x for horizontal)
            secondary: list[float] = []  # 찾아낸 축 좌표 (x for vertical, y for horizontal)

            for scan_pos in range(scan_start, scan_end, step):
                lo, hi = int(coord) - search_radius, int(coord) + search_radius
                if axis == "vertical":
                    line = gray[scan_pos, lo:hi].astype(float)
                else:
                    line = gray[lo:hi, scan_pos].astype(float)

                local_pos = cls._weighted_dark_centroid(line)
                if local_pos is None:
                    continue
                primary.append(scan_pos)
                secondary.append(lo + local_pos)

            if len(primary) < 10:
                return None, None

            primary_arr = np.array(primary)
            secondary_arr = np.array(secondary)
            slope, intercept = np.polyfit(primary_arr, secondary_arr, 1)

            # 이상치 제거 후 재피팅 (텍스트 라벨 등으로 인한 국소적 튐 방지)
            residual = secondary_arr - (slope * primary_arr + intercept)
            std = residual.std()
            if std > 1e-6:
                mask = np.abs(residual) < 2 * std
                if mask.sum() >= 10:
                    slope, intercept = np.polyfit(primary_arr[mask], secondary_arr[mask], 1)

            mid_scan = (scan_start + scan_end) / 2.0
            coord = slope * mid_scan + intercept  # 다음 반복은 이번 결과를 중심으로 재탐색

        return float(slope), float(intercept)

    @staticmethod
    def _weighted_dark_centroid(line: np.ndarray) -> float | None:
        """1차원 밝기 프로파일에서, 양 끝의 선형 배경(비네팅 등)을 제거한 뒤 어두운 정도를
        가중치로 한 무게중심(서브픽셀 위치, 0-based index)을 구한다. 대비가 거의 없으면 None."""
        if line.size < 7:
            return None
        idx = np.arange(len(line))
        edge_n = 3
        edge_idx = np.concatenate([idx[:edge_n], idx[-edge_n:]])
        edge_val = np.concatenate([line[:edge_n], line[-edge_n:]])
        slope, intercept = np.polyfit(edge_idx, edge_val, 1)
        baseline = slope * idx + intercept
        inv = np.clip(baseline - line, 0, None)  # 배경보다 어두운 만큼만 가중치로 사용
        if inv.sum() < 3:
            return None
        return float(np.sum(inv * idx) / np.sum(inv))

    def _detect_ticks(self, gray: np.ndarray, axis: str, axis_coord: float) -> list[float]:
        """축 선 바로 옆(수직 오프셋)의 얇은 띠에서 어두운 tick 돌출부의 위치를 찾는다.

        단순 구현(v1): 축과 평행한 좁은 띠를 잘라 평균 밝기 프로파일을 구하고,
        국소적으로 어두운 지점(tick)을 피크로 검출한다. 텍스트 라벨과 혼동될 수 있어
        캘리브레이션 UI의 수동 보정이 최종 확정 단계임을 전제로 한다.
        """
        profile = self.tick_strip(gray, axis, axis_coord)
        if profile is None:
            return []
        return self._find_dark_peaks(profile)

    @staticmethod
    def tick_strip(
        gray: np.ndarray, axis: str, axis_coord: float, band: int = 3, offset: int = 8
    ) -> np.ndarray | None:
        """축 선 바로 옆(살짝 떨어진 위치)의 얇은 띠를 잘라 평균 밝기 1차원 프로파일로
        반환한다 - `_detect_ticks()`와 `PixelAngleCalibration.refine_scale()`이 공용으로
        사용. axis="x": 가로축(수평선) 위의 tick들을 보기 위해 그 수평선과 나란한 띠를
        추출(프로파일은 x좌표에 따라 변함). axis="y": 세로축 옆 띠(프로파일은 y좌표에 따라
        변함). 반환하는 프로파일의 인덱스 0은 원본 이미지의 x=0(또는 y=0)에 대응한다.
        """
        h, w = gray.shape[:2]
        if axis == "x":
            y0 = int(max(0, axis_coord - offset - band))
            y1 = int(max(0, axis_coord - offset))
            if y1 <= y0:
                return None
            return gray[y0:y1, :].mean(axis=0)
        x0 = int(max(0, axis_coord - offset - band))
        x1 = int(max(0, axis_coord - offset))
        if x1 <= x0:
            return None
        return gray[:, x0:x1].mean(axis=1)

    @staticmethod
    def _find_dark_peaks(profile: np.ndarray, min_gap: int = 5) -> list[float]:
        if profile.size == 0:
            return []
        mean_val = profile.mean()
        threshold = mean_val - profile.std() * 0.5
        dark_mask = profile < threshold

        peaks: list[float] = []
        i = 0
        n = len(dark_mask)
        while i < n:
            if dark_mask[i]:
                j = i
                while j < n and dark_mask[j]:
                    j += 1
                peaks.append((i + j - 1) / 2.0)
                i = j
            else:
                i += 1

        # 너무 인접한 피크 병합
        merged: list[float] = []
        for p in peaks:
            if merged and (p - merged[-1]) < min_gap:
                continue
            merged.append(p)
        return merged
