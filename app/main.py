"""앱 진입점.

기본은 실제 IDS 카메라(IdsPeakCameraService)를 사용하지만, --mock 옵션으로 하드웨어 없이
개발/데모용 가상 카메라를 선택할 수 있다. 실카메라 연결을 시도했는데 실패하면(--mock을 주지
않았어도) 프로그램이 그냥 죽는 대신 자동으로 가상 카메라로 전환한다(사용자 요청, 2026-09-16 -
"카메라가 없으면 카메라 입력이 없어서 시험 모드로 진입합니다 안내 띄우고 --mock로 돌아가도록").
영상 파일로 절차를 검증하고 싶으면 프로그램을 켠 뒤(--mock이든 실카메라든 무관) "시험 진행"
탭의 "영상 선택" 버튼으로 아무 때나 불러올 수 있으므로, 예전에 시작 시점에 영상을 지정하던
--playback 옵션은 더 이상 필요 없어 제거했다(사용자 지적, 2026-09-16 - "PLAYBACK은 이제
의미없어, MOCK에서도 영상으로 시험 가능하잖아").

DB(결과 저장/조회)는 settings.json의 database.server/database가 채워져 있으면 자동으로
연결을 시도한다 - 비어 있거나 연결에 실패하면(pyodbc/드라이버 미설치, 서버 접속 불가 등)
결과 저장/조회 기능 없이(repository=None) 나머지 화면은 그대로 동작한다. --no-db로 DB
설정이 있어도 강제로 건너뛸 수 있다(하드웨어 없는 UI 데모 등).
"""
from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from app.viewmodels.inspection_viewmodel import InspectionViewModel
from app.views.main_window import MainWindow
from core.camera.camera_service import ICameraService
from core.config.settings import Settings, load_default_settings


def _build_camera(args: argparse.Namespace) -> ICameraService:
    if args.mock:
        from core.camera.mock_camera_service import MockCameraService

        return MockCameraService()

    from core.camera.ids_peak_camera_service import IdsPeakCameraService

    return IdsPeakCameraService(device_serial=args.device_serial or "")


def _build_camera_with_fallback(args: argparse.Namespace) -> ICameraService:
    """--mock을 명시하지 않았는데 실카메라 연결에 실패하면, 프로그램이 그냥 죽는 대신
    사용자에게 안내하고 자동으로 가상 카메라로 전환한다(사용자 요청, 2026-09-16) - 카메라가
    없는 PC(데모/교육용 등)에서도 절차 확인이 가능해야 한다는 취지. --mock을 이미 명시한
    경우는 애초에 실카메라를 열 필요가 없으므로 그대로 반환한다.

    실카메라를 미리 한 번 열어보고(open) 성공하면 곧바로 닫는다(close) - 실제 스트리밍
    시작은 InspectionViewModel.start_camera()가 나중에 다시 open()하는 시점에 한다(정상
    수명주기를 유지하기 위함). 여기서는 순전히 "카메라가 있는지" 확인 용도로만 연다.
    """
    camera = _build_camera(args)
    if args.mock:
        return camera

    try:
        camera.open()
    except Exception as exc:  # noqa: BLE001 - 드라이버 미설치/케이블 미연결 등 원인이 다양해 안내로 충분
        print(f"[camera] 카메라 연결 실패 - 시험 모드(가상 카메라)로 전환합니다: {exc}", file=sys.stderr)
        QMessageBox.warning(
            None,
            "카메라 연결 안 됨",
            "카메라 입력이 없어서 시험 모드로 진입합니다.\n(가상 카메라로 절차를 확인할 수 있습니다.)",
        )
        from core.camera.mock_camera_service import MockCameraService

        return MockCameraService()

    camera.close()
    return camera


def _build_repository(settings: Settings, args: argparse.Namespace):
    """settings.json의 database 설정으로 MSSQL 연결을 시도한다.

    server/database가 비어있거나(--no-db 또는 미설정) 연결에 실패하면 None을 반환 -
    "결과 조회" 탭이 화면에서 빠지고 "시험 종료"는 DB 저장 없이 확정만 하는 것으로
    InspectionViewModel.finalize_inspection()이 이미 처리한다.
    """
    if args.no_db:
        return None
    db = settings.database
    if not db.server or not db.database:
        return None

    from core.data.db_config import connect
    from core.data.repository import InspectionRepository

    try:
        connection = connect(db)
    except Exception as exc:  # noqa: BLE001 - 드라이버 미설치/접속 실패 등 다양한 원인을 화면 없이도 알 수 있게 로그만 남기고 계속 진행
        print(f"[DB] 연결 실패 - 결과 저장/조회 기능 없이 진행합니다: {exc}", file=sys.stderr)
        return None
    return InspectionRepository(connection)


def main() -> int:
    parser = argparse.ArgumentParser(description="조준경 불량 검사 프로그램")
    parser.add_argument("--mock", action="store_true", help="가상 카메라 사용 (하드웨어 없이 UI 개발/데모)")
    parser.add_argument("--device-serial", type=str, default=None, help="IDS 카메라 시리얼 번호")
    parser.add_argument(
        "--no-db", action="store_true", help="settings.json에 DB 설정이 있어도 연결하지 않음 (결과 저장/조회 없이 실행)"
    )
    args = parser.parse_args()

    settings = load_default_settings()

    # QMessageBox(카메라 연결 실패 안내)를 띄우려면 QApplication이 먼저 있어야 하므로,
    # 카메라를 열기 전에 앱부터 만든다(사용자 요청, 2026-09-16).
    app = QApplication(sys.argv)

    camera = _build_camera_with_fallback(args)
    repository = _build_repository(settings, args)

    viewmodel = InspectionViewModel(camera, settings, repository=repository)
    window = MainWindow(viewmodel, camera_id=args.device_serial or "default", repository=repository)
    # 작업표시줄이 있는 상태에서 최대화(풀스크린이 아님) - FHD 모니터 기준 대략 1920x1000
    # 정도의 사용 가능 영역이 되며, showMaximized()가 OS에 맞는 실제 작업 영역을 알아서 계산함
    window.showMaximized()

    viewmodel.start_camera()
    # 카메라가 열린 뒤(=start_camera() 이후)에만 실제 노드 값을 읽을 수 있다 - 시작 시
    # settings.json 값과 카메라 실제 상태가 다를 수 있으므로, 열리자마자 한 번 동기화해서
    # 카메라 설정 화면이 "진짜 현재값"을 보여주게 한다(사용자 요청, 2026-09-14).
    try:
        window.camera_settings_view.sync_from_camera()
    except Exception as exc:  # noqa: BLE001 - 동기화 실패해도 나머지 화면은 정상 진행
        print(f"[camera] 시작 시 설정 동기화 실패: {exc}", file=sys.stderr)

    exit_code = app.exec()
    viewmodel.stop_camera()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
