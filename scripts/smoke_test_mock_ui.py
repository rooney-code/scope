"""하드웨어 없이 전체 UI 배선을 검증하는 데모/스모크 테스트 스크립트.

MockCameraService로 레드닷을 점진적으로 이동시켜(BlobTracker의 max_jump_px 게이팅을
만족시키기 위해 - 실제 운영에서도 클릭 1회당 ~1MOA씩 점진적으로 이동하므로 이 방식이 현실적임)
'상' 방향 트래블 검사 1건을 처음부터 끝까지 통과시키는 과정을 화면 없이(offscreen) 재현한다.

주의: 카메라의 실제 백그라운드 스레드/실시간 타이밍에 의존하지 않고, 각 단계에서 프레임을
직접(동기적으로) 밀어넣어 결과가 sleep 시간이나 스레드 스케줄링에 흔들리지 않게 한다
(실시간 스레드 경로 자체는 test_playback_camera_service.py 등에서 별도로 검증됨).

실행: QT_QPA_PLATFORM=offscreen PYTHONPATH=. python3 scripts/smoke_test_mock_ui.py
"""
from __future__ import annotations

import sys
import time

from PySide6.QtWidgets import QApplication

from app.viewmodels.inspection_viewmodel import InspectionViewModel
from app.views.main_window import MainWindow
from core.calibration.grid_auto_detector import GridDetectionResult
from core.camera.mock_camera_service import MockCameraService
from core.config.settings import load_default_settings
from core.inspection.travel_test_state_machine import Phase


def _push_frame_at(camera: MockCameraService, vm: InspectionViewModel, x_moa: float, y_moa: float) -> None:
    """카메라 위치를 설정하고 렌더된 프레임 1장을 동기적으로 파이프라인에 직접 주입한다."""
    camera.set_dot_position_moa(x_moa, y_moa)
    frame = camera._render_frame()  # noqa: SLF001 - 테스트 전용 직접 호출
    vm._on_frame(frame)  # noqa: SLF001 - 백그라운드 스레드/타이밍 없이 동기 검증


def main() -> None:
    settings = load_default_settings()
    camera = MockCameraService()
    app = QApplication(sys.argv)

    vm = InspectionViewModel(camera, settings)
    window = MainWindow(vm, camera_id="SMOKE-TEST-CAM")
    window.show()

    camera.open()  # 스레드 캡처는 시작하지 않음 - 프레임을 직접 주입하므로 불필요

    window.scope_id_input.setText("SCOPE-SMOKE-001")
    assert vm.can_start_inspection(), "부품 ID 게이트 실패"

    vm.calibration.seed_from_auto_detection(
        "SMOKE-TEST-CAM", GridDetectionResult(found=True, origin_px=camera.origin_px())
    )
    vm.calibration.profile.px_per_moa_x = camera.px_per_moa()
    vm.calibration.profile.px_per_moa_y = camera.px_per_moa()

    for direction, checkbox in window.stage2_view._direction_checks.items():
        checkbox.setChecked(direction.value == "up")
    window.stage2_view._on_start()
    app.processEvents()
    assert vm.state_machine.phase == Phase.OUTBOUND

    # 0 -> 35 MOA 점진 이동 (클릭 1회당 ~5moa 스텝, jump 게이팅 통과), 각 스텝에서 프레임 몇 장씩 주입
    for y in range(0, 36, 5):
        for _ in range(3):
            _push_frame_at(camera, vm, 0, y)
    app.processEvents()

    window.stage2_view._on_far_point_reached()
    app.processEvents()
    if vm.state_machine.phase != Phase.RETURN:
        result = vm.state_machine.direction_results[-1]
        print(f"[디버그] 이동완료 후 예상과 다른 phase: {vm.state_machine.phase}, 판정={result.verdict}")
        for check in result.check_results:
            print(f"  {check.check_type.value}: {check.status.value} (측정값={check.measured_value}, 임계값={check.threshold_used})")
        return

    # 35 -> 0 MOA 점진 복귀
    for y in range(35, -1, -5):
        for _ in range(3):
            _push_frame_at(camera, vm, 0, y)
    app.processEvents()

    window.stage2_view._on_returned_to_origin()
    app.processEvents()

    assert vm.state_machine.phase == Phase.INSPECTION_DONE
    result = vm.state_machine.direction_results[-1]
    print(f"방향={result.direction}, 판정={result.verdict}")
    for check in result.check_results:
        print(f"  {check.check_type.value}: {check.status.value} (측정값={check.measured_value})")
    print(f"전체 판정: {vm.state_machine.overall_verdict}")
    print(f"결과 목록 UI 항목 수: {window.stage2_view.results_list.count()}")
    print("스모크 테스트 통과" if result.verdict.value == "합격" else "스모크 테스트 실패 (판정 불량)")


if __name__ == "__main__":
    main()
