"""트래블 시험 판정값/정지 판정 설정 화면.

지금까지는 이 값들(35MOA 목표, 각종 임계값, 정지 판정 대기시간 등)을 바꾸려면 settings.json을
직접 열어 편집해야 했다 - 카메라 설정처럼 이 값들도 프로그램 안에서 바꿀 수 있어야 한다는
요청(2026-09-16, "설정값은 사용자가 바꿔야 하는 부분이니 이 프로그램으로 해결하면 좋을것
같아")에 따라 카메라 설정 탭과 같은 자리(탭)에 둔다.

CameraSettingsView와 달리 하드웨어 노드에 쓰는 게 아니라 단순 파이썬 객체(Stage2Settings/
StabilitySettings) 필드라서, 값이 바뀌는 즉시(적용 버튼 없이) 그 자리에서 setattr로 반영한다 -
InspectionViewModel/TravelTestStateMachine은 이 dataclass 인스턴스를 그대로 참조해서 매번
속성을 읽으므로(예: self.settings.travel_target_moa) 별도 "적용" 호출이 필요 없다. 단,
settings.json 파일 자체에는 "저장" 버튼을 눌러야 기록된다 - 그래야 다음 실행에도 유지된다.
"""
from __future__ import annotations

from dataclasses import fields

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.config.settings import DEFAULT_SETTINGS_PATH, DetectionSettings, Settings, Stage2Settings, StabilitySettings

_STAGE2_LABELS = {
    "travel_target_moa": "목표 이동량 [MOA]",
    "drift_threshold_moa": "드리프트 임계값 [MOA]",
    "shift_threshold_moa": "쉬프트 임계값 [MOA]",
    "backlash_threshold_moa": "백래쉬 임계값 [MOA]",
    "near_zero_band_moa": "원점 복귀 판정 근접 범위 [MOA]",
    "stop_on_failure_scope": "불량 시 중단 범위",
    "auto_direction_threshold_moa": "자동 방향 인식 임계값 [MOA]",
}
_STABILITY_LABELS = {
    "window_size_samples": "정지 판정 샘플 수",
    "variance_threshold_moa2": "정지 판정 분산 임계값 [MOA²]",
    "min_stable_duration_s": "정지 판정 대기시간 [초]",
}
_STOP_ON_FAILURE_OPTIONS = ["entire_inspection", "direction_only"]
# 필드별로 소수점 자리수를 다르게 준다 - 기본(2자리)로는 분산 임계값(0.01)처럼 작은 값이나
# 대기시간(초 단위, 소수점 1자리면 충분) 표시가 어색하다.
_DECIMALS_OVERRIDE = {
    "min_stable_duration_s": 1,
    "variance_threshold_moa2": 4,
}


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-weight: bold; margin-top: 8px;")
    return label


class InspectionSettingsView(QWidget):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._widgets: dict[str, QWidget] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(_section_label("트래블 시험 판정값 (2단계)"))
        layout.addLayout(self._build_form(self.settings.stage2, _STAGE2_LABELS))
        layout.addWidget(_section_label("정지 판정 (원점 정렬 대기/목표 도달/원점 복귀 공통)"))
        layout.addLayout(self._build_form(self.settings.stability, _STABILITY_LABELS))

        # DetectionSettings는 hsv 튜플 등 이 화면이 다루지 않는 필드가 많아 _build_form()으로
        # 통째로 돌리지 않고, 필요한 필드 하나만 수동으로 추가한다(사용자 요청, 2026-09-16 -
        # "실제 운용 환경은 더 느릴 수 있어서 최소 N프레임당 1개는 처리하고 싶다").
        layout.addWidget(_section_label("성능 (프레임 처리)"))
        perf_form = QFormLayout()
        skip_widget = self._make_widget("adaptive_frame_skip_max", self.settings.detection.adaptive_frame_skip_max)
        self._widgets["adaptive_frame_skip_max"] = skip_widget
        self._connect_instant_apply("adaptive_frame_skip_max", skip_widget, self.settings.detection)
        perf_form.addRow("최대 프레임 스킵 (N프레임당 최소 1회는 처리)", skip_widget)
        layout.addLayout(perf_form)

        save_btn = QPushButton("저장 (settings.json에 기록 - 다음 실행에도 유지)")
        save_btn.clicked.connect(self._on_save)
        restore_btn = QPushButton("기본값 복원")
        restore_btn.clicked.connect(self._on_restore_defaults)
        layout.addWidget(save_btn)
        layout.addWidget(restore_btn)
        layout.addStretch(1)

    def _build_form(self, dataclass_obj, labels: dict[str, str]) -> QFormLayout:
        form = QFormLayout()
        for f in fields(dataclass_obj):
            widget = self._make_widget(f.name, getattr(dataclass_obj, f.name))
            self._widgets[f.name] = widget
            self._connect_instant_apply(f.name, widget, dataclass_obj)
            form.addRow(labels.get(f.name, f.name), widget)
        return form

    def _make_widget(self, name: str, value) -> QWidget:
        if name == "stop_on_failure_scope":
            combo = QComboBox()
            combo.addItems(_STOP_ON_FAILURE_OPTIONS)
            combo.setCurrentText(str(value))
            return combo
        if isinstance(value, int):  # bool은 이 화면의 대상 필드에 없음
            spin = QSpinBox()
            spin.setRange(0, 1_000_000)
            spin.setValue(value)
            return spin
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 1_000_000.0)
        spin.setDecimals(_DECIMALS_OVERRIDE.get(name, 2))
        spin.setValue(float(value))
        return spin

    def _connect_instant_apply(self, name: str, widget: QWidget, dataclass_obj) -> None:
        # 카메라 설정과 달리 하드웨어에 별도로 "적용"할 대상이 없다 - dataclass 필드 자체가
        # InspectionViewModel/TravelTestStateMachine이 매번 직접 읽는 값이므로, 값이 바뀌는
        # 즉시 setattr만 하면 그걸로 충분히 "적용"된다.
        if isinstance(widget, QComboBox):
            widget.currentTextChanged.connect(lambda text, n=name: setattr(dataclass_obj, n, text))
        elif isinstance(widget, QSpinBox):
            widget.valueChanged.connect(lambda value, n=name: setattr(dataclass_obj, n, value))
        elif isinstance(widget, QDoubleSpinBox):
            widget.valueChanged.connect(lambda value, n=name: setattr(dataclass_obj, n, value))

    def _on_save(self) -> None:
        self.settings.save(DEFAULT_SETTINGS_PATH)
        QMessageBox.information(self, "저장 완료", "settings.json에 저장했습니다.")

    def _on_restore_defaults(self) -> None:
        stage2_defaults = Stage2Settings()
        stability_defaults = StabilitySettings()
        for f in fields(Stage2Settings):
            default_value = getattr(stage2_defaults, f.name)
            setattr(self.settings.stage2, f.name, default_value)
            self._set_widget_value(self._widgets[f.name], default_value)
        for f in fields(StabilitySettings):
            default_value = getattr(stability_defaults, f.name)
            setattr(self.settings.stability, f.name, default_value)
            self._set_widget_value(self._widgets[f.name], default_value)
        default_skip = DetectionSettings().adaptive_frame_skip_max
        self.settings.detection.adaptive_frame_skip_max = default_skip
        self._set_widget_value(self._widgets["adaptive_frame_skip_max"], default_skip)

    @staticmethod
    def _set_widget_value(widget: QWidget, value) -> None:
        widget.blockSignals(True)
        try:
            if isinstance(widget, QComboBox):
                widget.setCurrentText(str(value))
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                widget.setValue(value)
        finally:
            widget.blockSignals(False)
