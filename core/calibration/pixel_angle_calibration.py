"""픽셀 <-> 각도(MOA) 변환 캘리브레이션.

GridAutoDetector의 자동 검출 결과를 초기값으로 받아, 작업자의 클릭 스냅(특정 tick을 클릭해
"이 위치가 몇 mrad/MOA인지" 지정)과 화살표 미세조정을 반영해 최종 원점과 px-per-MOA(X/Y 분리)
스케일을 확정한다. 카메라 식별자별로 JSON 프로파일을 저장/재사용한다(다중 장비 대응).

좌표계: 화면 픽셀은 y가 아래로 증가하지만, 검사 도메인에서는 "위(up)"가 +Y 이므로
to_moa()에서 y축 부호를 반전한다.

**px_per_moa 정밀도**: `snap_tick()`은 클릭 1점만으로 스케일을 계산하므로, 그 한 점의 측정
오차가 원점에서 먼 지점(예: 35MOA)일수록 그대로 확대되어 보이는 문제가 있었다(실측으로 확인 -
가까운 곳은 잘 맞는데 35MOA 근처에서 눈금이 벌어짐). `refine_scale()`은 근처의 보조눈금(1MOA
간격, 원본 그리드에 인쇄된 것)을 수십 개 정밀(서브픽셀) 측정해 최소자승으로 스케일을 다시
구해 이 오차를 크게 줄인다 - snap_tick() 이후 선택적으로 호출.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from core.calibration.grid_auto_detector import GridAutoDetector, GridDetectionResult


@dataclass
class CalibrationProfile:
    camera_id: str
    origin_px_x: float
    origin_px_y: float
    px_per_moa_x: float
    px_per_moa_y: float

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "CalibrationProfile":
        return CalibrationProfile(**d)


class PixelAngleCalibration:
    def __init__(self, mrad_to_moa_ratio: float = 3.438) -> None:
        self.mrad_to_moa_ratio = mrad_to_moa_ratio
        self.profile: CalibrationProfile | None = None
        # refine_scale()이 마지막으로 정밀 측정에 성공했을 때 사용한 보조눈금 개수
        # (진단/로그 출력용 - 판정 로직에는 쓰이지 않음)
        self.last_refine_tick_count: int | None = None

    # ---- 초기 추정(자동 검출 결과로부터) ----
    def seed_from_auto_detection(self, camera_id: str, grid_result: GridDetectionResult) -> None:
        if not grid_result.found or grid_result.origin_px is None:
            raise ValueError("자동 검출 결과가 유효하지 않습니다 (found=False 또는 origin 없음).")
        ox, oy = grid_result.origin_px
        # px-per-moa는 아직 알 수 없으므로 임시값(추후 snap_tick으로 확정 필요)
        self.profile = CalibrationProfile(
            camera_id=camera_id, origin_px_x=ox, origin_px_y=oy, px_per_moa_x=1.0, px_per_moa_y=1.0
        )

    # ---- 수동 보정: 클릭 스냅 ----
    def snap_tick(self, tick_px: float, known_value: float, unit: str, axis: str) -> None:
        """작업자가 클릭한 tick의 픽셀 좌표와 그 tick이 나타내는 실제 값(mrad 또는 moa)을 받아
        px-per-MOA 스케일을 계산한다.

        axis: 'x' 또는 'y'. tick_px는 해당 축 방향의 절대 픽셀 좌표(원점과 같은 좌표계).
        """
        if self.profile is None:
            raise RuntimeError("먼저 seed_from_auto_detection()으로 초기값을 설정하세요.")

        value_moa = known_value * self.mrad_to_moa_ratio if unit == "mrad" else known_value
        if value_moa == 0:
            raise ValueError("known_value가 0이면 스케일을 계산할 수 없습니다 (원점과 같은 tick은 제외).")

        origin = self.profile.origin_px_x if axis == "x" else self.profile.origin_px_y
        px_per_moa = abs(tick_px - origin) / abs(value_moa)

        if axis == "x":
            self.profile.px_per_moa_x = px_per_moa
        else:
            self.profile.px_per_moa_y = px_per_moa

    # ---- 정밀 보정: 보조눈금 다수를 이용한 스케일 재측정 ----
    def refine_scale(
        self,
        gray_frame: np.ndarray,
        axis: str,
        tick_unit_moa: float = 1.0,
        k_range: int = 40,
        search_radius: int = 4,  # 창 크기(2*radius)가 7 미만이면 _weighted_dark_centroid가
        # 가장자리 샘플만으로 기준선을 추정하지 못해 항상 None을 반환하므로 4 이상 필요
        iterations: int = 4,
        min_ticks: int = 10,
    ) -> bool:
        """원점 주변에 인쇄된 보조눈금(기본 1MOA 간격)을 수십 개 정밀(서브픽셀) 측정해
        최소자승으로 px-per-MOA를 다시 구한다. snap_tick()의 클릭 1점 측정은 그 한 점의
        오차가 먼 지점일수록 그대로 확대되어 보이는 문제가 있었음(실측 확인) - 이 메서드는
        원점을 고정한 채 여러 보조눈금까지의 거리를 함께 맞춰 오차를 평균화한다.

        gray_frame: 그레이스케일(단일 채널) 원본 프레임. axis: 'x' 또는 'y'.
        현재 px_per_moa 값을 초기 추정치로 사용해 각 보조눈금의 예상 위치를 계산하고,
        그 근방(±search_radius)에서 실제 보조눈금을 찾아 반복 수렴시킨다.
        측정 가능한 보조눈금이 min_ticks개 미만이면 기존 값을 그대로 두고 False를 반환한다.
        """
        if self.profile is None:
            raise RuntimeError("먼저 seed_from_auto_detection()으로 초기값을 설정하세요.")

        if axis == "x":
            origin = self.profile.origin_px_x
            axis_coord = self.profile.origin_px_y
            slope = self.profile.px_per_moa_x * tick_unit_moa
        else:
            origin = self.profile.origin_px_y
            axis_coord = self.profile.origin_px_x
            slope = self.profile.px_per_moa_y * tick_unit_moa

        strip = GridAutoDetector.tick_strip(gray_frame, axis=axis, axis_coord=axis_coord)
        if strip is None:
            return False

        # 초기 slope(px_per_moa)에 오차가 있으면 먼 tick(큰 |k|)일수록 예측 위치 오차가
        # 커져서, search_radius 창이 엉뚱한 인접 tick을 붙잡는 "앨리어싱"이 발생할 수 있다
        # (tick 간격이 촘촘할 때 특히 그러함 - 실측에서 발견). 가까운 tick부터 시작해 slope를
        # 먼저 다듬고, 그 다듬어진 slope로 예측 오차를 줄인 뒤에야 더 먼 tick까지 범위를
        # 넓히는 점진적 확장으로 앨리어싱을 방지한다.
        found_k: np.ndarray = np.array([])
        found_pos: np.ndarray = np.array([])
        cur_k_range = min(k_range, search_radius * 2)
        done_iterations = 0
        max_attempts = iterations + 6  # 범위 확장 재시도가 있어 iterations보다 여유를 둠
        for _attempt in range(max_attempts):
            if done_iterations >= iterations:
                break
            found_k_list: list[int] = []
            found_pos_list: list[float] = []
            for k in range(-cur_k_range, cur_k_range + 1):
                if k == 0:
                    continue
                predicted = origin + k * slope
                lo, hi = int(round(predicted)) - search_radius, int(round(predicted)) + search_radius
                if lo < 0 or hi > len(strip):
                    continue
                center = GridAutoDetector._weighted_dark_centroid(strip[lo:hi])
                if center is None:
                    continue
                pos = lo + center
                if abs(pos - predicted) <= search_radius:
                    found_k_list.append(k)
                    found_pos_list.append(pos)

            if len(found_k_list) < min_ticks:
                self.last_refine_tick_count = len(found_k_list)
                if cur_k_range < k_range:
                    # 근처 tick이 충분치 않으면(예: 원점 부근 tick 일부 누락) 탐색 범위를
                    # 넓혀 같은 iteration을 다시 시도 - 아직 slope를 갱신하지 않았으므로
                    # 앨리어싱 위험 없이 더 먼 tick까지 포함해볼 수 있다.
                    cur_k_range = min(k_range, cur_k_range * 2)
                    continue
                return False

            found_k = np.array(found_k_list, dtype=float)
            found_pos = np.array(found_pos_list, dtype=float)
            rel_pos = found_pos - origin
            slope = float(np.sum(found_k * rel_pos) / np.sum(found_k**2))

            resid = rel_pos - slope * found_k
            std = resid.std()
            if std > 1e-6:
                mask = np.abs(resid) < 2 * std
                if mask.sum() >= min_ticks:
                    found_k, rel_pos = found_k[mask], rel_pos[mask]
                    slope = float(np.sum(found_k * rel_pos) / np.sum(found_k**2))

            self.last_refine_tick_count = len(found_k)
            done_iterations += 1
            cur_k_range = min(k_range, cur_k_range * 2)

        if done_iterations == 0:
            return False

        self.last_refine_tick_count = len(found_k)
        px_per_moa = slope / tick_unit_moa
        if axis == "x":
            self.profile.px_per_moa_x = px_per_moa
        else:
            self.profile.px_per_moa_y = px_per_moa
        return True

    # ---- 수동 보정: 화살표 미세조정 (1px 단위) ----
    def nudge_origin(self, dx_px: float = 0.0, dy_px: float = 0.0) -> None:
        if self.profile is None:
            raise RuntimeError("먼저 seed_from_auto_detection()으로 초기값을 설정하세요.")
        self.profile.origin_px_x += dx_px
        self.profile.origin_px_y += dy_px

    # ---- 변환 ----
    def to_moa(self, px_point: tuple[float, float]) -> tuple[float, float]:
        if self.profile is None:
            raise RuntimeError("캘리브레이션이 설정되지 않았습니다.")
        px, py = px_point
        p = self.profile
        x_moa = (px - p.origin_px_x) / p.px_per_moa_x
        y_moa = -(py - p.origin_px_y) / p.px_per_moa_y  # 화면 y 반전 (위 = +Y)
        return x_moa, y_moa

    # ---- 영속화 (카메라별 프로파일) ----
    def save(self, directory: str | Path) -> None:
        if self.profile is None:
            raise RuntimeError("저장할 캘리브레이션이 없습니다.")
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{self.profile.camera_id}.json"
        path.write_text(json.dumps(self.profile.to_dict(), indent=2), encoding="utf-8")

    def load(self, directory: str | Path, camera_id: str) -> bool:
        path = Path(directory) / f"{camera_id}.json"
        if not path.exists():
            return False
        self.profile = CalibrationProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return True
