"""설정 로더: settings.json <-> dataclass.

계획 문서(logical-herding-teapot.md)의 설정 스키마를 그대로 반영한다.
모든 판정 임계값/카메라 기본값은 여기서 관리하며 하드코딩하지 않는다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CameraSettings:
    device_serial: str = ""
    frame_rate_fps: float = 50.01
    exposure_time_us: float = 15000.0
    exposure_limited_by_frame_rate: bool = True
    device_link_throughput_limit_bps: int = 350_000_000
    analog_gain: float = 2.00
    digital_gain_r: float = 1.00
    digital_gain_g: float = 1.00
    digital_gain_b: float = 1.00
    black_level_auto: str = "off"
    black_level: float = 0.03
    auto_function_owner: str = "host"
    auto_exposure: str = "off"
    auto_gain: str = "off"
    brightness_roi_frame_skip: int = 2
    brightness_roi: str = "full"
    brightness_percentile: float = 13.00
    brightness_target: float = 150
    brightness_tolerance: float = 3
    auto_white_balance: str = "off"
    wb_gain_r: float = 1.00
    wb_gain_g: float = 1.00
    wb_gain_b: float = 1.00
    color_correction_host: str = "off"
    color_correction_matrix_preset: str = "HQ"
    saturation_enabled: bool = False
    saturation: float = 1.00
    chromatic_adaption_enabled: bool = False
    chromatic_adaption_algorithm: str = "Bradford"
    chromatic_adaption_color_space: str = "sRGB D65"
    chromatic_adaption_color_temp_k: float = 6500


@dataclass
class DatabaseSettings:
    server: str = ""
    database: str = ""
    auth_mode: str = "sql"  # "sql" | "windows"
    user: str = ""
    password: str = ""  # 운영 시 환경변수/자격증명 저장소 사용 권장, settings.json에 평문 저장 금지


@dataclass
class CalibrationSettings:
    mrad_to_moa_ratio: float = 3.438  # 1 mrad ~= 3.438 MOA (10 mrad ~= 34.4 MOA)


@dataclass
class DetectionSettings:
    # 참고: docs/detection_notes.md - 레드닷은 순수 빨강이 아니라 호박색(amber)에 가까움
    hsv_lower1: tuple[int, int, int] = (5, 80, 120)
    hsv_upper1: tuple[int, int, int] = (35, 255, 255)
    # 채도/명도 하한을 hsv_lower1과 동일하게 유지 - 0으로 두면 배경의 어둡고 무채색인 픽셀까지
    # (hue만 우연히 0~4 범위인) 매칭되어 노이즈 블롭이 레드닷과 합쳐지는 문제가 있었음(실기 테스트로 확인)
    hsv_lower2: tuple[int, int, int] = (0, 80, 120)
    hsv_upper2: tuple[int, int, int] = (4, 255, 255)
    min_blob_area: float = 15.0
    max_blob_jump_px: float = 60.0  # BlobTracker 게이팅, 현장 튜닝 필요


@dataclass
class Stage1Settings:
    tolerance_moa: float = 0.5
    stable_duration_ms: float = 500.0


@dataclass
class Stage2Settings:
    travel_target_moa: float = 35.0
    drift_threshold_moa: float = 2.5
    shift_threshold_moa: float = 2.5
    backlash_threshold_moa: float = 2.5
    # TravelTestStateMachine.feed_position()이 "원점 복귀를 마쳤다"고 자동 판단하는 근접
    # 범위. 백래쉬 자체가 0에서 벗어난 잔류오차를 재는 값이라, 이 밴드가 backlash_threshold_moa
    # 보다 좁으면 실제 백래쉬 불량(0에서 여러 MOA 떨어짐)을 자동으로 못 잡아낸다 - 이동 중간에
    # 잠깐 멈추는 것과는 확실히 구분되면서도 불량 판정 범위는 덮도록 여유를 둔 값.
    near_zero_band_moa: float = 5.0
    stop_on_failure_scope: str = "entire_inspection"  # "entire_inspection" | "direction_only"


@dataclass
class StabilitySettings:
    window_size_samples: int = 8
    variance_threshold_moa2: float = 0.01
    min_stable_duration_ms: float = 150.0


@dataclass
class DirectionQueueSettings:
    default_order: list[str] = field(default_factory=lambda: ["up", "down", "left", "right"])


@dataclass
class Settings:
    camera: CameraSettings = field(default_factory=CameraSettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    calibration: CalibrationSettings = field(default_factory=CalibrationSettings)
    detection: DetectionSettings = field(default_factory=DetectionSettings)
    stage1: Stage1Settings = field(default_factory=Stage1Settings)
    stage2: Stage2Settings = field(default_factory=Stage2Settings)
    stability: StabilitySettings = field(default_factory=StabilitySettings)
    direction_queue: DirectionQueueSettings = field(default_factory=DirectionQueueSettings)

    @staticmethod
    def load(path: str | Path) -> "Settings":
        p = Path(path)
        if not p.exists():
            return Settings()
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return Settings(
            camera=CameraSettings(**data.get("camera", {})),
            database=DatabaseSettings(**data.get("database", {})),
            calibration=CalibrationSettings(**data.get("calibration", {})),
            detection=DetectionSettings(**_tuplify_hsv(data.get("detection", {}))),
            stage1=Stage1Settings(**data.get("stage1", {})),
            stage2=Stage2Settings(**data.get("stage2", {})),
            stability=StabilitySettings(**data.get("stability", {})),
            direction_queue=DirectionQueueSettings(**data.get("direction_queue", {})),
        )

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")


def _tuplify_hsv(detection_dict: dict[str, Any]) -> dict[str, Any]:
    out = dict(detection_dict)
    for key in ("hsv_lower1", "hsv_upper1", "hsv_lower2", "hsv_upper2"):
        if key in out and out[key] is not None:
            out[key] = tuple(out[key])
    return out


DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "settings.json"


def load_default_settings() -> Settings:
    return Settings.load(DEFAULT_SETTINGS_PATH)
