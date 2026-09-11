from PySide6.QtWidgets import QApplication
import pytest

from app.viewmodels.inspection_viewmodel import InspectionViewModel
from core.camera.mock_camera_service import MockCameraService
from core.config.settings import load_default_settings
from core.data.repository import InspectionRepository
from core.inspection.models import TravelDirection, Verdict
from tests.fake_mssql import FakeMssqlConnection


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_vm(repository=None) -> InspectionViewModel:
    vm = InspectionViewModel(MockCameraService(), load_default_settings(), repository=repository)
    vm.set_scope_id("SCOPE-VM-TEST")
    return vm


def _pass_direction(vm: InspectionViewModel, direction: TravelDirection) -> None:
    """상태기계를 직접 조작해 한 방향을 합격으로 완료시킨다 (카메라/검출 파이프라인 없이
    뷰모델의 finalize/retest 배선만 검증하는 용도)."""
    vm.state_machine.start_next_direction()
    vm.state_machine._max_primary_reached = 35.0
    vm.mark_far_point_reached()
    vm.mark_returned_to_origin()


def test_finalize_without_repository_just_locks_state_machine():
    vm = _make_vm(repository=None)
    vm.configure_directions([TravelDirection.UP])
    _pass_direction(vm, TravelDirection.UP)

    assert vm.is_ready_to_finalize
    overall = vm.finalize_inspection()

    assert overall == Verdict.PASS.value
    assert vm.state_machine.finalized
    assert not vm.is_ready_to_finalize


def test_finalize_with_repository_saves_full_session():
    conn = FakeMssqlConnection()
    repo = InspectionRepository(conn)
    vm = _make_vm(repository=repo)
    vm.configure_directions([TravelDirection.UP])
    _pass_direction(vm, TravelDirection.UP)

    vm.finalize_inspection()

    assert len(conn.sessions) == 1
    session_id = next(iter(conn.sessions))
    assert conn.sessions[session_id]["ScopeId"] == "SCOPE-VM-TEST"
    assert conn.sessions[session_id]["OverallVerdict"] == Verdict.PASS.value
    assert conn.sessions[session_id]["CompletedAt"] is not None
    assert len(conn.direction_results) == 1


def test_retest_direction_delegates_to_state_machine():
    vm = _make_vm()
    vm.configure_directions([TravelDirection.UP])
    vm.state_machine._max_primary_reached = 15.0  # 이동량 미달로 실패시킴
    vm.state_machine.start_next_direction()
    vm.mark_far_point_reached()

    assert vm.state_machine.direction_results[-1].verdict == Verdict.FAIL

    vm.retest_direction(TravelDirection.UP)

    assert vm.state_machine.direction_results == []
    assert TravelDirection.UP in vm.state_machine.direction_queue
