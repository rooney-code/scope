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
    # 이 카메라(U3-388xLE-C)는 BalanceRatio 노드 자체가 없다(실기 확인 사항) - 카메라 측에
    # 화이트밸런스 게인을 걸 방법이 없으므로, IdsPeakCameraService가 ids_peak_ipl.Gain으로
    # 캡처된 각 프레임에 직접 적용한다(호스트 측/소프트웨어 구현, docs/windows_setup_guide.md
    # 참고). 더 이상 TBD 아님 - 실측으로 R/G/B 채널별 독립 적용 확인됨.
    # 유효 범위는 1.0~8.0(실측 확인) - 1.0 미만으로는 감쇠 불가하다. 특정 채널를 "덜 붉게/
    # 덜 파랗게" 만들고 싶으면 그 채널을 낮추는 게 아니라 나머지 채널들을 올려 상대적으로
    # 맞추는 방식으로 값을 잡아야 한다(범위를 벗어난 값은 IdsPeakCameraService가 자동으로
    # 1.0~8.0 범위로 clamp함).
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
    # 레티클 구조상 중심에서 멀어질수록(특히 35MOA 근처) 레드닷이 원형에서 "코멧테일"
    # 형태(밝은 머리 + 중심 반대쪽으로 흐려지는 꼬리)로 변형됨 - 실측 영상으로 확인된 제품
    # 특성(docs/detection_notes.md 참고). 윤곽선 전체를 동일 가중치로 취급하는 단순 무게중심은
    # 꼬리 쪽으로 쏠려 중심점이 밀리므로, 밝기로 가중치를 줘서 밝은 "머리" 부분이 중심 계산을
    # 지배하도록 한다. centroid_intensity_power를 올릴수록 꼬리의 영향이 더 억제됨(1.0=밝기
    # 그대로 가중, 커질수록 밝은 부분에 더 집중). 0으로 두면 기존 방식(이진 마스크 무게중심)과
    # 동일해짐.
    centroid_intensity_power: float = 2.0
    # "코멧테일" 꼬리는 어두운 산란광일 뿐 실제 레드닷(LED 광원) 자체는 항상 원형이다(고객
    # 확인 사항, docs/detection_notes.md 10차 참고). fitEllipse는 꼬리까지 포함한 윤곽선
    # 전체로 형상을 구해 늘어진 타원으로 보이므로, `RedDotDetector._fit_head_square()`가
    # 바운딩박스를 짧은 변(늘어지지 않는 방향의 폭) 크기의 정사각형들로 나눠 그중 가장 밝은
    # 정사각형을 "머리"(core_circle)로 판단한다(사용자 제안, 2026-09-13 - 밝기 임계값 튜닝이
    # 필요 없는 순수 기하학적 방법. docs/detection_notes.md 12차 후속 수정 참고).
    # BlobTracker의 심한 왜곡 구간 보정(추적) 설정 - 사용자 확인 사항(2026-09-13): 정상적인
    # 원형 구간(대부분의 프레임)은 프레임별 측정치를 그대로 신뢰하고, elongation(늘어짐
    # 비율)이 임계값을 넘는 심한 코멧테일 구간에서만 이전 프레임들로 추정한 속도 기반
    # 예측 위치와 현재 측정치를 blend해 흔들림을 줄인다. docs/detection_notes.md 10차 참고.
    #
    # 기본값이 blend=0.0(사실상 비활성화)인 이유(2026-09-13 후속 확인, 14차 참고): 13차에서
    # 예측 근거를 원시 측정치로 고정해 누적 드리프트는 해결했지만, 이후 center_px 자체가
    # `_fit_head_square()`(정사각형 분할, 코멧테일에도 흔들림 없이 안정적)로 바뀌면서 원시
    # 측정치 자체가 이미 충분히 정확해져 이 보정이 필요 없어짐 - 오히려 보정이 이미 정확한
    # 값을 (직전 두 프레임의 속도 추정 오차만큼) 미세하게 틀어지게 만드는 경우가 실측으로
    # 확인됨. 코드/메커니즘은 남겨두고(추후 다른 왜곡 패턴에서 필요해질 수 있음) 기본값만
    # 끔 - 필요 시 설정으로 다시 켤 수 있음.
    elongation_correction_threshold: float = 1.5
    elongation_correction_blend: float = 0.0


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
