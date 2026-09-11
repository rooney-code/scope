"""앱 진입점.

기본은 실제 IDS 카메라(IdsPeakCameraService)를 사용하지만, --mock/--playback 옵션으로
하드웨어 없이 개발/데모용 카메라를 선택할 수 있다.
"""
from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication

from app.viewmodels.inspection_viewmodel import InspectionViewModel
from app.views.main_window import MainWindow
from core.camera.camera_service import ICameraService
from core.config.settings import load_default_settings


def _build_camera(args: argparse.Namespace) -> ICameraService:
    if args.mock:
        from core.camera.mock_camera_service import MockCameraService

        return MockCameraService()
    if args.playback:
        from core.camera.playback_camera_service import PlaybackCameraService

        return PlaybackCameraService(args.playback)

    from core.camera.ids_peak_camera_service import IdsPeakCameraService

    return IdsPeakCameraService(device_serial=args.device_serial or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="조준경 불량 검사 프로그램")
    parser.add_argument("--mock", action="store_true", help="가상 카메라 사용 (하드웨어 없이 UI 개발/데모)")
    parser.add_argument("--playback", type=str, default=None, help="정지 이미지/영상 파일을 카메라로 재생")
    parser.add_argument("--device-serial", type=str, default=None, help="IDS 카메라 시리얼 번호")
    args = parser.parse_args()

    settings = load_default_settings()
    camera = _build_camera(args)

    app = QApplication(sys.argv)

    viewmodel = InspectionViewModel(camera, settings)
    window = MainWindow(viewmodel, camera_id=args.device_serial or "default")
    window.resize(1400, 900)
    window.show()

    viewmodel.start_camera()

    exit_code = app.exec()
    viewmodel.stop_camera()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
