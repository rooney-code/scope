from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication

from app.views.camera_settings_view import CameraSettingsView
from core.config.settings import CameraSettings


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeCamera:
    """카메라 노드 상태를 흉내내는 더블 - 일부 필드만 실제로 바꾸고, 일부는 읽기전용으로
    표시하는 read_settings()를 검증하기 위함(사용자 요청: 카메라 현재값 동기화, 2026-09-14)."""

    def __init__(self, camera_frame_rate: float, read_only: set[str]) -> None:
        self._camera_frame_rate = camera_frame_rate
        self._read_only = read_only

    def apply_settings(self, settings) -> None:  # noqa: ANN001
        pass

    def apply_host_settings(self, settings) -> None:  # noqa: ANN001
        pass

    def read_settings(self, base: CameraSettings) -> tuple[CameraSettings, set[str]]:
        result = replace(base, frame_rate_fps=self._camera_frame_rate)
        return result, self._read_only


class _RecordingCamera:
    """apply_settings()/apply_host_settings() 호출 횟수를 기록하는 더블 - 소프트웨어
    필드는 적용 버튼 없이 즉시(apply_host_settings만), 하드웨어 필드는 적용 버튼을 눌러야만
    (apply_settings) 반영되는지 확인하기 위함(사용자 요청, 2026-09-15)."""

    def __init__(self) -> None:
        self.apply_settings_calls = 0
        self.apply_host_settings_calls = 0

    def apply_settings(self, settings) -> None:  # noqa: ANN001
        self.apply_settings_calls += 1

    def apply_host_settings(self, settings) -> None:  # noqa: ANN001
        self.apply_host_settings_calls += 1

    def read_settings(self, base: CameraSettings) -> tuple[CameraSettings, set[str]]:
        return base, set()


def test_software_field_change_applies_instantly_without_apply_button():
    """소프트웨어 그룹(예: wb_gain_r - 카메라 노드가 아니라 호스트에서만 처리) 위젯 값을
    바꾸면 적용 버튼 없이 바로 apply_host_settings()가 호출돼야 하고, apply_settings()
    (하드웨어 노드 쓰기, 스트리밍 정지가 필요한 무거운 경로)는 호출되면 안 된다."""
    camera = _RecordingCamera()
    view = CameraSettingsView(camera, CameraSettings())

    view._widgets["wb_gain_r"].setValue(2.5)

    assert camera.apply_host_settings_calls >= 1
    assert camera.apply_settings_calls == 0
    assert view.settings.wb_gain_r == 2.5


def test_hardware_field_change_requires_apply_button():
    """하드웨어 그룹(예: frame_rate_fps - 실제 카메라 노드) 위젯 값을 바꾸는 것만으로는
    아무것도 호출되지 않고, "적용" 버튼(_on_apply)을 눌러야만 apply_settings()가
    호출돼야 한다."""
    camera = _RecordingCamera()
    view = CameraSettingsView(camera, CameraSettings())

    view._widgets["frame_rate_fps"].setValue(42.0)
    assert camera.apply_settings_calls == 0
    assert camera.apply_host_settings_calls == 0

    view._on_apply()
    assert camera.apply_settings_calls == 1
    assert view.settings.frame_rate_fps == 42.0


class _ClampingCamera:
    """apply_host_settings()가 유효 범위를 벗어난 wb_gain_r을 클램프해서 settings를 직접
    고치는(IdsPeakCameraService._update_white_balance_gain과 동일한 패턴) 더블 - 화면이
    사용자가 입력한 원래 값이 아니라 실제 적용된(클램프된) 값을 보여줘야 함을 검증
    (사용자 지적: UI 값과 실제 적용값이 소리 없이 달라지는 문제, 2026-09-15)."""

    def apply_settings(self, settings) -> None:  # noqa: ANN001
        pass

    def apply_host_settings(self, settings) -> None:  # noqa: ANN001
        settings.wb_gain_r = min(settings.wb_gain_r, 8.0)  # 실측 유효 상한 흉내

    def read_settings(self, base: CameraSettings) -> tuple[CameraSettings, set[str]]:
        return base, set()


def test_software_field_widget_reflects_clamped_applied_value():
    view = CameraSettingsView(_ClampingCamera(), CameraSettings())

    view._widgets["wb_gain_r"].setValue(12.0)  # 유효 범위(1.0~8.0) 밖

    assert view.settings.wb_gain_r == pytest.approx(8.0)  # 클램프된 값이 저장됨
    assert view._widgets["wb_gain_r"].value() == pytest.approx(8.0)  # 화면도 같은 값


def test_sync_from_camera_updates_settings_and_widget_values():
    """"카메라에서 불러오기"는 카메라가 읽어준 값으로 self.settings와 위젯을 덮어써야
    한다 - 화면에서 편집 중이던 값은 버려진다."""
    settings = CameraSettings(frame_rate_fps=10.0)
    camera = _FakeCamera(camera_frame_rate=42.0, read_only=set())
    view = CameraSettingsView(camera, settings)

    # 사용자가 값을 편집했다고 가정(동기화로 버려져야 함)
    view._widgets["frame_rate_fps"].setValue(999.0)

    view.sync_from_camera()

    assert settings.frame_rate_fps == 42.0
    assert view._widgets["frame_rate_fps"].value() == 42.0


def test_sync_from_camera_disables_read_only_fields():
    """카메라가 read_only로 표시한 필드는 위젯이 비활성화되어야 한다 - 적용해도 소용없는
    값을 사용자가 편집하지 못하게(사용자 요청, 2026-09-14)."""
    settings = CameraSettings()
    camera = _FakeCamera(camera_frame_rate=30.0, read_only={"auto_exposure", "wb_gain_r"})
    view = CameraSettingsView(camera, settings)

    view.sync_from_camera()

    assert not view._widgets["auto_exposure"].isEnabled()
    assert not view._widgets["wb_gain_r"].isEnabled()
    assert view._widgets["frame_rate_fps"].isEnabled()


def test_sync_from_camera_reenables_previously_disabled_fields():
    """이전 동기화에서 비활성화됐던 필드가 이번엔 read_only에 없으면 다시 활성화되어야
    한다(예: 카메라 재연결 등으로 상태가 바뀐 경우)."""
    settings = CameraSettings()
    view = CameraSettingsView(_FakeCamera(camera_frame_rate=30.0, read_only={"auto_exposure"}), settings)
    view.camera = _FakeCamera(camera_frame_rate=30.0, read_only={"auto_exposure"})
    view.sync_from_camera()
    assert not view._widgets["auto_exposure"].isEnabled()

    view.camera = _FakeCamera(camera_frame_rate=30.0, read_only=set())
    view.sync_from_camera()
    assert view._widgets["auto_exposure"].isEnabled()
