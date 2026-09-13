"""MSSQL CRUD (pyodbc). 완료(평가까지 도달한) 시도만 저장 - 시험중지로 폐기된 시도는 저장하지 않음.

연결(Connection)은 생성자에서 주입받는다(의존성 주입) - db_config.connect()로 만든 실제
pyodbc.Connection이든, 테스트용 스텁이든 동일한 커서 인터페이스(execute/fetchone/fetchall/commit)만
있으면 동작한다. 모든 쿼리는 파라미터화(placeholder)해서 SQL 인젝션을 방지한다.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from core.inspection.models import DirectionTestResult, InspectionSession, Verdict


class InspectionRepository:
    def __init__(self, connection) -> None:
        self._conn = connection

    def ensure_scope(self, scope_id: str) -> None:
        cur = self._conn.cursor()
        cur.execute("SELECT ScopeId FROM Scopes WHERE ScopeId = ?", (scope_id,))
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO Scopes (ScopeId, CreatedAt) VALUES (?, ?)",
                (scope_id, _utcnow()),
            )
        self._conn.commit()

    def create_session(self, scope_id: str, operator: str) -> str:
        self.ensure_scope(scope_id)
        session_id = str(uuid.uuid4())
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO InspectionSessions
                (SessionId, ScopeId, Operator, StartedAt, CompletedAt, OverallVerdict, CalibrationSnapshotJson)
            VALUES (?, ?, ?, ?, NULL, ?, NULL)
            """,
            (session_id, scope_id, operator, _utcnow(), Verdict.IN_PROGRESS.value),
        )
        self._conn.commit()
        return session_id

    def set_calibration_snapshot(self, session_id: str, calibration_profile_dict: dict) -> None:
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE InspectionSessions SET CalibrationSnapshotJson = ? WHERE SessionId = ?",
            (json.dumps(calibration_profile_dict, ensure_ascii=False), session_id),
        )
        self._conn.commit()

    def complete_session(self, session_id: str, overall_verdict: Verdict) -> None:
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE InspectionSessions SET CompletedAt = ?, OverallVerdict = ? WHERE SessionId = ?",
            (_utcnow(), overall_verdict.value, session_id),
        )
        self._conn.commit()

    def save_direction_result(self, session_id: str, result: DirectionTestResult) -> int:
        """완료된(중지로 폐기되지 않은) 방향 결과 1건과 그 하위 CheckResults를 저장.

        반환값: DirectionResults.Id (자동증가 PK).
        """
        start_x, start_y = result.start_point_moa if result.start_point_moa is not None else (None, None)
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO DirectionResults
                (SessionId, Direction, AttemptNumber, Verdict, StartPointXMoa, StartPointYMoa, CreatedAt)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, result.direction.value, result.attempt_number, result.verdict.value, start_x, start_y, _utcnow()),
        )
        direction_result_id = self._fetch_last_identity(cur)

        for check in result.check_results:
            cur.execute(
                """
                INSERT INTO CheckResults (DirectionResultId, CheckType, MeasuredValue, RawMeasuredValue, ThresholdUsed, Status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    direction_result_id,
                    check.check_type.value,
                    check.measured_value,
                    check.raw_measured_value,
                    check.threshold_used,
                    check.status.value,
                ),
            )
        self._conn.commit()
        return direction_result_id

    def save_full_session(self, session: InspectionSession, session_id: str) -> None:
        """편의 메서드: 이미 만들어진 세션ID에 대해 방향별 결과를 일괄 저장 후 세션 종료 처리."""
        for direction_result in session.direction_results:
            self.save_direction_result(session_id, direction_result)
        self.complete_session(session_id, session.overall_verdict)

    def get_sessions_by_scope(self, scope_id: str) -> list[dict]:
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT SessionId, ScopeId, Operator, StartedAt, CompletedAt, OverallVerdict
            FROM InspectionSessions
            WHERE ScopeId = ?
            ORDER BY StartedAt DESC
            """,
            (scope_id,),
        )
        columns = [c[0] for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    @staticmethod
    def _fetch_last_identity(cursor) -> int:
        cursor.execute("SELECT @@IDENTITY")
        row = cursor.fetchone()
        return int(row[0])


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
