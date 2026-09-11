"""픽셀 <-> 각도(MOA) 변환 캘리브레이션.

GridAutoDetector의 자동 검출 결과를 초기값으로 받아, 작업자의 클릭 스냅(특정 tick을 클릭해
"이 위치가 몇 mrad/MOA인지" 지정)과 화살표 미세조정을 반영해 최종 원점과 px-per-MOA(X/Y 분리)
스케일을 확정한다. 카메라 식별자별로 JSON 프로파일을 저장/재사용한다(다중 장비 대응).

좌표계: 화면 픽셀은 y가 아래로 증가하지만, 검사 도메인에서는 "위(up)"가 +Y 이므로
to_moa()에서 y축 부호를 반전한다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from core.calibration.grid_auto_detector import GridDetectionResult


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
