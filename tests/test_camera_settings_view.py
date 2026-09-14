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

    def read_settings(self, base: CameraSettings) -> tuple[CameraSettings, set[str]]:
        result = replace(base, frame_rate_fps=self._camera_frame_rate)
        return result, self._read_only


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
