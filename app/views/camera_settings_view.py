"""카메라 파라미터 화면. 현장 캡처 값(계획서 표)을 기본값으로 로드하고,
'기본값 복원' 버튼으로 언제든 되돌릴 수 있다."""
from __future__ import annotations

from dataclasses import fields

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.camera.camera_service import ICameraService
from core.config.settings import CameraSettings


_ON_OFF_FIELDS = {
    "black_level_auto",
    "auto_exposure",
    "auto_gain",
    "auto_white_balance",
    "color_correction_host",
}

# IDS peak Cockpit의 한글 UI 명칭과 맞춰둔다 - 실제 작업자는 Cockpit의 한글 명칭에 익숙하므로
# 필드명(영문 변수명)을 그대로 노출하는 대신 이 표기를 쓴다(사용자 요청, 2026-09-14).
# 매핑에 없는 필드는 폴백으로 영문 필드명을 그대로 보여준다.
_FIELD_LABELS_KO = {
    "device_serial": "카메라 시리얼 번호",
    "frame_rate_fps": "프레임률 [fps]",
    "exposure_time_us": "노출 시간 [µs]",
    "exposure_limited_by_frame_rate": "프레임률에 따른 노출 시간 제한",
    "device_link_throughput_limit_bps": "처리량 제한 [바이트/초]",
    "analog_gain": "아날로그 게인",
    "digital_gain_r": "디지털 게인 (R)",
    "digital_gain_g": "디지털 게인 (G)",
    "digital_gain_b": "디지털 게인 (B)",
    "black_level_auto": "블랙 레벨 자동 조정",
    "black_level": "흑색 레벨",
    "auto_function_owner": "자동 기능 (카메라/호스트)",
    "auto_exposure": "Auto exposure",
    "auto_gain": "Auto gain",
    "brightness_roi_frame_skip": "프레임 건너뛰기",
    "brightness_roi": "자동 밝기 ROI",
    "brightness_percentile": "백분위수 [%]",
    "brightness_target": "대상",
    "brightness_tolerance": "허용 오차",
    "auto_white_balance": "자동 화이트 밸런스",
    # 이 카메라의 NodeMap에는 BalanceRatio 계열 노드가 없어(scripts/list_camera_nodes.py로
    # 실기 확인, 2026-09-14) 카메라 쪽엔 적용 안 되지만, ids_peak_camera_service.py가
    # ids_peak_ipl.Gain으로 호스트(PC) 측에서 직접 프레임에 게인을 곱하는 방식으로
    # 구현되어 있다(_update_white_balance_gain/_apply_white_balance_gain 참고) - 실제로
    # 반영되니 "빨강/초록/파랑"이 카메라의 다른 값들과 동일하게 정상 적용된다.
    "wb_gain_r": "빨강",
    "wb_gain_g": "초록",
    "wb_gain_b": "파랑",
    "color_correction_host": "색상 보정 호스트",
    "color_correction_matrix_preset": "색상 보정 매트릭스",
    "saturation_enabled": "채도 사용",
    "saturation": "채도",
    "chromatic_adaption_enabled": "Chromatic adaption 사용",
    "chromatic_adaption_algorithm": "Algorithm",
    "chromatic_adaption_color_space": "Color space",
    "chromatic_adaption_color_temp_k": "Color temperature [K]",
}


class CameraSettingsView(QWidget):
    def __init__(self, camera: ICameraService, settings: CameraSettings, parent=None) -> None:
        super().__init__(parent)
        self.camera = camera
        self.settings = settings
        self._widgets: dict[str, QWidget] = {}

        # CameraSettings 필드가 28개라 한 줄(QFormLayout 하나)로 쭉 펼치면 탭 하나가
        # 800px+ 높이를 요구하게 된다 - QTabWidget은 "현재 보이는 탭"이 아니라 모든 탭
        # 중 가장 큰 요구 크기로 창 전체의 최소 높이를 정하므로, 이 탭 때문에 FHD
        # 모니터에서 창 자체가 화면보다 커져 레이아웃이 찌그러지는 문제가 있었다(실측으로
        # 확인된 버그). 스크롤 영역으로 감쌌던 적도 있으나 매번 스크롤해야 해서 불편하다는
        # 피드백이 있어(2026-09-14), 대신 필드를 반으로 나눠 2열로 배치해 필요 높이를
        # 절반으로 줄인다.
        all_fields = list(fields(CameraSettings))
        half = (len(all_fields) + 1) // 2
        columns = QHBoxLayout()
        for column_fields in (all_fields[:half], all_fields[half:]):
            form = QFormLayout()
            for f in column_fields:
                widget = self._make_widget(f.name, getattr(settings, f.name))
                self._widgets[f.name] = widget
                form.addRow(_FIELD_LABELS_KO.get(f.name, f.name), widget)
            columns.addLayout(form)

        apply_btn = QPushButton("적용 (카메라에 반영)")
        apply_btn.clicked.connect(self._on_apply)
        restore_btn = QPushButton("기본값 복원")
        restore_btn.clicked.connect(self._on_restore_defaults)
        # 카메라의 실제 현재값을 읽어와 화면에 반영 - 편집 중이던 값은 버려진다(사용자
        # 요청: 시작 시 자동으로, 그리고 버튼으로도 동기화, 2026-09-14). 적용해도 카메라에
        # 반영 안 되는 필드(연동 안 됨/지금 상태에서 불가)는 이때 자동으로 비활성화된다.
        sync_btn = QPushButton("카메라에서 불러오기 (현재 편집값 무시)")
        sync_btn.clicked.connect(self.sync_from_camera)

        layout = QVBoxLayout(self)
        layout.addLayout(columns)
        layout.addWidget(apply_btn)
        layout.addWidget(restore_btn)
        layout.addWidget(sync_btn)
        layout.addStretch(1)

    def _make_widget(self, name: str, value) -> QWidget:
        if name in _ON_OFF_FIELDS:
            combo = QComboBox()
            combo.addItems(["off", "on", "host", "once"])
            combo.setCurrentText(str(value))
            return combo
        if isinstance(value, bool):
            box = QCheckBox()
            box.setChecked(value)
            return box
        if isinstance(value, (int, float)):
            spin = QDoubleSpinBox()
            spin.setRange(-1_000_000_000, 1_000_000_000)
            spin.setDecimals(4)
            spin.setValue(float(value))
            return spin
        line = QLineEdit(str(value))
        return line

    def _read_widget(self, name: str, widget: QWidget, default):
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QDoubleSpinBox):
            return type(default)(widget.value()) if isinstance(default, int) else widget.value()
        if isinstance(widget, QLineEdit):
            return widget.text()
        return default

    @staticmethod
    def _set_widget_value(widget: QWidget, value) -> None:
        if isinstance(widget, QComboBox):
            widget.setCurrentText(str(value))
        elif isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value))
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value))

    def _on_apply(self) -> None:
        for f in fields(CameraSettings):
            current_default = getattr(self.settings, f.name)
            new_value = self._read_widget(f.name, self._widgets[f.name], current_default)
            setattr(self.settings, f.name, new_value)
        self.camera.apply_settings(self.settings)

    def _on_restore_defaults(self) -> None:
        defaults = CameraSettings()
        for f in fields(CameraSettings):
            default_value = getattr(defaults, f.name)
            setattr(self.settings, f.name, default_value)
            self._set_widget_value(self._widgets[f.name], default_value)

    def sync_from_camera(self) -> None:
        """카메라의 현재 값을 읽어와 화면(및 self.settings)을 덮어쓴다 - 편집 중이던
        값은 버려진다. 프로그램 시작 시 자동 호출(app/main.py)과 "카메라에서 불러오기"
        버튼이 이 메서드를 공유한다. 카메라에 적용해도 의미 없는 필드(연동 안 됨/지금
        상태에서 불가)는 위젯을 비활성화해서 편집 자체를 막는다(사용자 요청: 적용 안
        되는 항목을 굳이 바꾸게 두는 건 의미 없는 변경이다, 2026-09-14)."""
        result, read_only = self.camera.read_settings(self.settings)
        for f in fields(CameraSettings):
            value = getattr(result, f.name)
            setattr(self.settings, f.name, value)
            widget = self._widgets[f.name]
            self._set_widget_value(widget, value)
            widget.setEnabled(f.name not in read_only)
