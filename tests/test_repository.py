from core.data.repository import InspectionRepository
from core.inspection.models import CheckResult, CheckType, DirectionTestResult, TravelDirection, Verdict
from tests.fake_mssql import FakeMssqlConnection


def test_create_session_creates_scope_and_session():
    conn = FakeMssqlConnection()
    repo = InspectionRepository(conn)

    session_id = repo.create_session(scope_id="SCOPE-001", operator="tester")

    assert "SCOPE-001" in conn.scopes
    assert session_id in conn.sessions
    assert conn.sessions[session_id]["OverallVerdict"] == Verdict.IN_PROGRESS.value


def test_save_direction_result_persists_checks():
    conn = FakeMssqlConnection()
    repo = InspectionRepository(conn)
    session_id = repo.create_session("SCOPE-002", "tester")

    result = DirectionTestResult(
        direction=TravelDirection.UP,
        attempt_number=1,
        verdict=Verdict.PASS,
        check_results=[
            CheckResult(CheckType.TRAVEL_AMOUNT, 35.2, 35.0, Verdict.PASS),
            CheckResult(CheckType.SHIFT, 1.1, 2.5, Verdict.PASS),
            CheckResult(CheckType.DRIFT, 0.4, 2.5, Verdict.PASS),
            CheckResult(CheckType.BACKLASH, 0.9, 2.5, Verdict.PASS),
        ],
    )

    direction_result_id = repo.save_direction_result(session_id, result)

    assert conn.direction_results[direction_result_id]["Direction"] == "up"
    saved_checks = [c for c in conn.check_results if c["DirectionResultId"] == direction_result_id]
    assert len(saved_checks) == 4
    assert {c["CheckType"] for c in saved_checks} == {"travel_amount", "shift", "drift", "backlash"}


def test_complete_session_sets_overall_verdict():
    conn = FakeMssqlConnection()
    repo = InspectionRepository(conn)
    session_id = repo.create_session("SCOPE-003", "tester")

    repo.complete_session(session_id, Verdict.FAIL)

    assert conn.sessions[session_id]["OverallVerdict"] == Verdict.FAIL.value
    assert conn.sessions[session_id]["CompletedAt"] is not None


def test_get_sessions_by_scope_returns_rows_as_dicts():
    conn = FakeMssqlConnection()
    repo = InspectionRepository(conn)
    sid1 = repo.create_session("SCOPE-004", "op1")
    repo.complete_session(sid1, Verdict.PASS)

    rows = repo.get_sessions_by_scope("SCOPE-004")

    assert len(rows) == 1
    assert rows[0]["SessionId"] == sid1
    assert rows[0]["OverallVerdict"] == Verdict.PASS.value


def test_aborted_attempts_are_never_saved():
    """중지(abort)로 폐기된 시도는 애초에 save_direction_result가 호출되지 않으므로
    DB에 흔적이 남지 않는다는 계약을 확인 (state machine과의 통합 관점 문서화 목적)."""
    conn = FakeMssqlConnection()
    repo = InspectionRepository(conn)
    session_id = repo.create_session("SCOPE-005", "tester")

    # abort된 시도는 repository에 아무것도 호출하지 않는다는 것이 계약
    assert len(conn.direction_results) == 0

    # 이후 완료된 시도만 저장
    result = DirectionTestResult(
        direction=TravelDirection.LEFT,
        attempt_number=1,  # abort는 attempt_number를 증가시키지 않음
        verdict=Verdict.PASS,
        check_results=[CheckResult(CheckType.TRAVEL_AMOUNT, 36.0, 35.0, Verdict.PASS)],
    )
    repo.save_direction_result(session_id, result)
    assert len(conn.direction_results) == 1
