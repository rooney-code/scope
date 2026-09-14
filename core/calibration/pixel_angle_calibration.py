"""픽셀 <-> 각도(MOA) 변환 캘리브레이션.

GridAutoDetector의 자동 검출(또는 작업자가 영상을 직접 클릭한 수동 검출)로 초기 원점을 얻고,
화살표 미세조정(nudge_origin/nudge_scale)으로 원점과 px-per-MOA(X/Y 분리) 스케일을 화면을
보면서 확정한다. 카메라 식별자별로 JSON 프로파일을 저장/재사용한다(다중 장비 대응).

과거에는 tick을 클릭하고 값을 입력하는 snap_tick()이 UI에 노출되어 스케일을 정하는 주된
수단이었으나, 그 폼의 값/축 입력이 헷갈린다는 피드백으로 UI에서 없앴다(2026-09-14) - 이제는
nudge_scale()이 스케일을 확정하는 유일한 UI 경로다. snap_tick()/refine_scale()은 API로는
남아있고 테스트에서 계속 쓰이지만, 화면 버튼으로는 더 이상 호출되지 않는다.

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

# 원점만 잡고 스케일을 아직 모를 때 쓰는 시작 추정치 - 실제 값은 렌즈/설치 상태에 따라 보통
# 6~20 px/MOA 범위이므로, 그 중간값 근처를 잡아두면 nudge_scale()로 다듬을 때 몇 번만
# 눌러도 실제값에 근접한다(클릭 스냅을 없애면서 nudge_scale이 유일한 스케일 확정 수단이
# 됐으므로, 1.0 같은 극단값에서 시작하면 조정에 수백 번 클릭이 필요해 비현실적이었음,
# 2026-09-14).
_DEFAULT_PX_PER_MOA_GUESS = 10.0


@dataclass
class CalibrationProfile:
    camera_id: str
    origin_px_x: float
    origin_px_y: float
    px_per_moa_x: float
    px_per_moa_y: float
    # 자동/수동 검출 직후에는 원점만 알고 px_per_moa는 대략적인 시작 추정치
    # (_DEFAULT_PX_PER_MOA_GUESS)다 - 이 상태로 크롭/MOA 계산을 하면 극단적으로 확대되거나
    # 터무니없는 오차 숫자가 나온다(실측으로 확인된 문제, 2026-09-14). nudge_scale()(또는
    # snap_tick/refine_scale)로 해당 축의 스케일이 실제 확정된 뒤에만 그 축의 플래그가
    # True - x/y를 따로 두는 이유는 이 확정 수단들이 한 번에 한 축만 다루기 때문(한쪽만
    # 조정해놓고 다른 쪽은 그대로 추정치인 상태를 구분해야 함).
    scale_confirmed_x: bool = False
    scale_confirmed_y: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "CalibrationProfile":
        # 과거(scale_confirmed_x/y 필드 도입 전)에 저장된 프로파일 파일과의 호환을 위해
        # 필드가 없으면 dataclass 기본값(False)으로 채운다 - 모르는 키는 무시.
        known_fields = {
            "camera_id", "origin_px_x", "origin_px_y", "px_per_moa_x", "px_per_moa_y",
            "scale_confirmed_x", "scale_confirmed_y",
        }
        return CalibrationProfile(**{k: v for k, v in d.items() if k in known_fields})


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
        self._seed_origin(camera_id, ox, oy)

    # ---- 초기 추정(작업자가 영상에서 직접 클릭한 위치로) ----
    def seed_from_manual_origin(self, camera_id: str, origin_px: tuple[float, float]) -> None:
        """자동 검출이 실패하거나(어두운 화면 등) 신뢰할 수 없을 때, 작업자가 캘리브레이션
        탭 영상에서 직접 클릭한 위치를 원점의 시작값으로 쓴다 - seed_from_auto_detection과
        동일하게 스케일은 아직 임시값이며, 원점도 화살표 미세조정으로 다듬는 것을 전제로 한다
        (사용자 요청, 2026-09-14: 클릭으로 대략적인 원점을 잡고 미세조정하는 워크플로)."""
        ox, oy = origin_px
        self._seed_origin(camera_id, ox, oy)

    def _seed_origin(self, camera_id: str, ox: float, oy: float) -> None:
        # px-per-moa는 아직 정확히 모르므로 대략적인 시작 추정치(_DEFAULT_PX_PER_MOA_GUESS)로
        # 채운다 - nudge_scale()로 확정 필요. scale_confirmed_x/y가 False라 is_ready가
        # False를 반환하고, 영상 크롭/오차 계산에 이 추정치가 쓰이지 않는다(is_ready 참고).
        self.profile = CalibrationProfile(
            camera_id=camera_id,
            origin_px_x=ox,
            origin_px_y=oy,
            px_per_moa_x=_DEFAULT_PX_PER_MOA_GUESS,
            px_per_moa_y=_DEFAULT_PX_PER_MOA_GUESS,
            scale_confirmed_x=False,
            scale_confirmed_y=False,
        )

    @property
    def is_ready(self) -> bool:
        """원점 + x/y 스케일이 모두 확정되어 크롭/MOA 계산에 안전하게 쓸 수 있는 상태인지."""
        return self.profile is not None and self.profile.scale_confirmed_x and self.profile.scale_confirmed_y

    def clear(self) -> None:
        """원점을 완전히 지운다 - 잘못 잡은 원점을 화살표로 되돌리기엔 너무 멀리 벗어났을 때,
        처음부터 다시 자동/수동 검출을 하기 위한 초기화 용도(사용자 요청, 2026-09-14).
        캘리브레이션 탭은 profile이 None이면 크롭하지 않고 원본 전체를 보여주므로
        (LiveFeedView._is_cropped_view), 이 호출만으로 화면도 자연스럽게 전체 보기로
        돌아간다."""
        self.profile = None

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
            self.profile.scale_confirmed_x = True
        else:
            self.profile.px_per_moa_y = px_per_moa
            self.profile.scale_confirmed_y = True

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
            self.profile.scale_confirmed_x = True
        else:
            self.profile.px_per_moa_y = px_per_moa
            self.profile.scale_confirmed_y = True
        return True

    # ---- 수동 보정: 화살표 미세조정 (기본 0.5px 단위) ----
    def nudge_origin(self, dx_px: float = 0.0, dy_px: float = 0.0) -> None:
        if self.profile is None:
            raise RuntimeError("먼저 seed_from_auto_detection()으로 초기값을 설정하세요.")
        self.profile.origin_px_x += dx_px
        self.profile.origin_px_y += dy_px

    # ---- 수동 보정: 눈금 간격(px-per-MOA) 미세조정 ----
    def nudge_scale(self, delta_x: float = 0.0, delta_y: float = 0.0) -> None:
        """작업자가 화면에서 빨간 좌표축 눈금이 실제 그리드와 맞는지 보면서 px_per_moa를
        직접 조정할 때 사용. 클릭 스냅을 없애면서(2026-09-14, 사용자 요청) 이 메서드가 스케일을
        확정하는 유일한 수단이 됐으므로, delta가 0이 아닌 축은 그 즉시 scale_confirmed로
        표시한다(자동 검출 직후의 임시 추정치를 사용자가 실제로 들여다보고 조정했다는 뜻)."""
        if self.profile is None:
            raise RuntimeError("먼저 seed_from_auto_detection()으로 초기값을 설정하세요.")
        if delta_x != 0:
            self.profile.px_per_moa_x += delta_x
            self.profile.scale_confirmed_x = True
        if delta_y != 0:
            self.profile.px_per_moa_y += delta_y
            self.profile.scale_confirmed_y = True

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
