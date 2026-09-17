"""설정 로더: settings.json <-> dataclass.

계획 문서(logical-herding-teapot.md)의 설정 스키마를 그대로 반영한다.
모든 판정 임계값/카메라 기본값은 여기서 관리하며 하드코딩하지 않는다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.app_paths import get_app_base_dir


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
    # 실제 레드닷 면적은 대략 500~550px2 정도(실측 이미지 주석 기준) - 조명/색보정 상태에
    # 따라 배경(초록 발광 영역) 전체가 HSV 임계값에 걸려 수백만 px2짜리 거대한 가짜 블롭이
    # 잡히는 경우가 있었다(실측으로 확인, 2026-09-14: candidates 1등이 4,323,714px2로
    # 프레임의 2/3를 차지). 진짜 레드닷보다 훨씬 큰 블롭은 애초에 후보에서 제외한다 -
    # "코멧테일" 왜곡(늘어진 형태)까지 감안해 넉넉히 잡되, 배경 전체 수준은 확실히 배제.
    max_blob_area: float = 5000.0
    # 면적만으로는 진짜 레드닷과 비슷한 크기의 가짜 후보(그리드 문자/눈금 반사 등)를 구분
    # 못하는 경우가 실측으로 확인됐다(2026-09-14: 진짜 522px2, 가짜 553px2 - 면적 차이가
    # 거의 없어 면적순 정렬만으로는 가짜가 이김). 레드닷(LED)은 둥근 반면 문자/눈금 반사
    # 같은 오검출은 보통 삐죽삐죽하거나 길쭉해서 원형도(circularity = 4π·area/perimeter^2,
    # 완전한 원=1.0)가 뚜렷이 낮다 - 이 기준으로 후보를 추가로 걸러낸다.
    #
    # 주의: 처음엔 0.5로 잡았는데, 이동 범위 끝에서 자연스럽게 늘어지는 "코멧테일" 진짜
    # 레드닷까지 걸러내는 회귀가 실측으로 확인됐다(2026-09-14) - _fit_head_square()가
    # 정확히 이런 길쭉한 모양을 전제로 설계된 기존 로직인데, 원형도 필터가 그 전에 후보
    # 자체를 쳐내버린 것. 텍스트/눈금처럼 정말 삐죽삐죽한 것만 걸러내도록 훨씬 낮췄다 -
    # 실측으로 진짜 코멧테일 모양의 원형도 실측값을 확인해 더 정교하게 다듬을 필요가 있음.
    min_circularity: float = 0.15
    # 면적/원형도만으로는 부족했다 - 모폴로지 CLOSE가 근처의 작은 HSV 매칭 조각들(글자
    # 획 등)을 이어붙이면서, 그 사이의 임계값 미달 어두운 배경 픽셀까지 윤곽선 안에
    # 끌려들어오는 경우가 있다(실측으로 확인, 2026-09-14: 문자/눈금 반사 후보들이 HSV
    # 마스크는 통과했는데 정작 윤곽선 내부 최대 밝기(V채널)는 2에 불과했음). 반면 진짜
    # LED는 꽉 찬 밝은 원이라 peak_v=255로 포화됨 - 150배 이상 차이로 매우 뚜렷한 구분
    # 기준이라 여유 있게(255의 절반 이상) 임계값을 잡아도 안전하다.
    min_peak_brightness: int = 130
    # 캘리브레이션(원점+스케일)이 확정된 이후에는 레드닷이 절대 벗어날 수 없는 범위가
    # 명확하다 - travel_target_moa(35)를 넘어서까지 검출 영역을 열어둘 이유가 없다.
    # 검출 영역을 원점 기준 이 반경(MOA)으로 제한하면 (1) 화면 먼 쪽의 문자/눈금 반사가
    # 애초에 검출 대상에서 제외되고 (2) 처리할 픽셀 수가 줄어 매 프레임 처리 시간도
    # 준다(사용자 요청, 2026-09-14). travel_target_moa(35)보다 여유를 둬서 약간의
    # 오버슈트/백래쉬로 목표치를 넘어서는 경우도 놓치지 않게 한다.
    roi_margin_moa: float = 45.0
    max_blob_jump_px: float = 60.0  # BlobTracker 게이팅, 현장 튜닝 필요
    # BlobTracker가 "직전 위치" 참조를 유지하는 최대 경과 시간(초) - 영상 탐색(seek)처럼
    # 조작에 실제 시간이 걸리는 불연속 상황에서, 한참 전(예: 되감기 전) 위치를 기준으로
    # 계속 게이팅해 거부하는 문제가 있었다(사용자 요청, 2026-09-15: "1초 정도만 참조").
    # 이보다 오래되면 다음 검출을 첫 프레임처럼(게이팅 없이) 받아들인다.
    max_position_reference_age_s: float = 1.0
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
    # 레드닷을 못 찾는 상황이 실제 시험 중 발생해서(영상 시뮬레이션으로 절차를 검증하며
    # 확인, 2026-09-15), 매 프레임 왜 검출/미검출됐는지(면적/원형도/밝기 중 어디서 후보가
    # 제외됐는지, 트래커가 왜 거부했는지 등)를 콘솔에 남기기 위한 토글. 기본은 꺼둔다 -
    # 매 프레임 로그를 남기면 정상 운영 중엔 콘솔이 감당 못 할 정도로 쏟아지므로, 문제를
    # 재현/분석할 때(특히 영상 시뮬레이션으로 특정 프레임을 반복 확인할 때)만 켠다.
    debug_logging: bool = False
    # 검출+상태기계 처리 시간(avg_frame_processing_ms)이 카메라 프레임 주기보다 길어지면
    # (개발 PC는 평균 3ms지만 실제 운용 PC는 더 느릴 수 있다는 우려, 사용자 요청 2026-09-16)
    # 계속 밀리기만 하지 않도록 일부 프레임을 건너뛴다 - 처리 시간이 여유 있으면 건너뛰지
    # 않고 매 프레임 처리하고, 느려질수록 필요한 만큼만 건너뛰되 이 값(기본 10)을 넘겨
    # 건너뛰지는 않는다 - "아무리 느려도 N프레임당 1개는 처리한다"는 마지노선. 영상 표시
    # (frame_ready)는 이 스킵과 무관하게 항상 매 프레임 그대로 나간다 - 건너뛴 프레임 동안은
    # 오버레이(레드닷 마커 등)만 마지막 처리 결과로 고정되고, 영상 자체는 끊기지 않는다.
    adaptive_frame_skip_max: int = 10


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
    # 시험 시작 버튼/방향 버튼 없이도, 원점 부근에서 안정적으로 멈춰 있던 마지막 위치(대기
    # baseline) 대비 한 축(x 또는 y)으로 이만큼 벗어나면 그 축+부호로 방향을 자동 판정해
    # 해당 방향 시험을 시작한다(사용자 요청, 2026-09-16 - 매 방향마다 버튼을 누르는 번거로움을
    # 줄이기 위함). 1차는 단순 임계값 방식 - 노이즈로 인한 오탐지가 실측으로 확인되면 방향
    # 일관성 체크 등을 추가할 수 있음.
    auto_direction_threshold_moa: float = 3.0


@dataclass
class StabilitySettings:
    window_size_samples: int = 8
    variance_threshold_moa2: float = 0.01
    # TravelTestStateMachine의 원점 복귀/목표 도달 자동 판정과 대기 중 baseline 추적에 쓰인다.
    # 예전엔 여기 값이 실제로 어디에도 연결되지 않은 죽은 설정이었다(TravelTestStateMachine이
    # 150ms로 하드코딩된 자체 기본값을 썼음) - 이번에 연결했다(사용자 요청, 2026-09-16).
    # 작업자가 프로그램을 최대한 안 건드리고 자동화하려 할 때, "여기서 멈췄다"는 신호를 주는
    # 데 필요한 시간으로 3초를 기본값으로 잡았다 - 너무 짧으면 이동 중 손 고쳐잡는 것도
    # 멈춤으로 오판할 수 있음. 단위는 초(sec) - ms 단위는 설정 화면에서 다루기 어색하다는
    # 지적(2026-09-16)에 따라 바꿨다. StabilityDetector 자체는 내부적으로 여전히 ms 단위를
    # 쓰므로(정밀한 단위 테스트에 유리) 경계(InspectionViewModel)에서 1000을 곱해 변환한다.
    min_stable_duration_s: float = 3.0


@dataclass
class DirectionQueueSettings:
    default_order: list[str] = field(default_factory=lambda: ["up", "down", "left", "right"])


@dataclass
class ReportSettings:
    # 결과 엑셀 파일명을 결정하는 검사 장비 ID(영문+숫자 권장, 예: "TI001") - 결과 파일을
    # "YYYYMM_장비ID.xlsx"(예: "202609_TI001.xlsx")로 장비별/월별로 나눠서 하나의 파일이
    # 무한정 커지는 것을 막는다(사용자 요청, 2026-09-17). settings.json에 저장/관리되며
    # "검사 설정" 탭에서 배포 후에도 바꿀 수 있다 - 배포 기본값은 "TI_default"로 두고,
    # 현장에서 실제 장비 ID로 바꿔 쓰는 것을 전제로 한다.
    equipment_id: str = "TI_default"


@dataclass
class Settings:
    camera: CameraSettings = field(default_factory=CameraSettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    calibration: CalibrationSettings = field(default_factory=CalibrationSettings)
    detection: DetectionSettings = field(default_factory=DetectionSettings)
    stage2: Stage2Settings = field(default_factory=Stage2Settings)
    stability: StabilitySettings = field(default_factory=StabilitySettings)
    direction_queue: DirectionQueueSettings = field(default_factory=DirectionQueueSettings)
    report: ReportSettings = field(default_factory=ReportSettings)

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
            stage2=Stage2Settings(**data.get("stage2", {})),
            stability=StabilitySettings(**data.get("stability", {})),
            direction_queue=DirectionQueueSettings(**data.get("direction_queue", {})),
            report=ReportSettings(**data.get("report", {})),
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


DEFAULT_SETTINGS_PATH = get_app_base_dir() / "settings.json"


def load_default_settings() -> Settings:
    return Settings.load(DEFAULT_SETTINGS_PATH)
