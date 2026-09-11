"""실제 MSSQL 서버 없이 InspectionRepository를 검증하기 위한 가짜 커넥션.

pyodbc.Connection/Cursor의 최소 인터페이스(execute/fetchone/fetchall/description/commit)만
구현하며, 계획서의 T-SQL 스키마를 in-memory 파이썬 구조로 흉내낸다.
실제 MSSQL 문법(NEWID, SYSUTCDATETIME 등) 검증은 하지 않으며, 이는 실기 서버에서의
통합 테스트가 필요함을 의미한다(계획서 TBD 항목).
"""
from __future__ import annotations


class FakeCursor:
    def __init__(self, db: "FakeMssqlConnection") -> None:
        self._db = db
        self._last_result: list[tuple] = []
        self.description: list[tuple] = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        sql_norm = " ".join(sql.split()).upper()
        self._last_result = []
        self.description = []

        if sql_norm.startswith("SELECT SCOPEID FROM SCOPES"):
            (scope_id,) = params
            row = self._db.scopes.get(scope_id)
            self._last_result = [(row,)] if row else []
        elif sql_norm.startswith("INSERT INTO SCOPES"):
            scope_id, created_at = params
            self._db.scopes[scope_id] = scope_id
        elif sql_norm.startswith("INSERT INTO INSPECTIONSESSIONS"):
            session_id, scope_id, operator, started_at, overall_verdict = params
            self._db.sessions[session_id] = {
                "SessionId": session_id,
                "ScopeId": scope_id,
                "Operator": operator,
                "StartedAt": started_at,
                "CompletedAt": None,
                "OverallVerdict": overall_verdict,
                "CalibrationSnapshotJson": None,
            }
        elif sql_norm.startswith("UPDATE INSPECTIONSESSIONS SET CALIBRATIONSNAPSHOTJSON"):
            snapshot, session_id = params
            self._db.sessions[session_id]["CalibrationSnapshotJson"] = snapshot
        elif sql_norm.startswith("UPDATE INSPECTIONSESSIONS SET COMPLETEDAT"):
            completed_at, overall_verdict, session_id = params
            self._db.sessions[session_id]["CompletedAt"] = completed_at
            self._db.sessions[session_id]["OverallVerdict"] = overall_verdict
        elif sql_norm.startswith("INSERT INTO DIRECTIONRESULTS"):
            session_id, direction, attempt_number, verdict, created_at = params
            self._db.direction_id_seq += 1
            new_id = self._db.direction_id_seq
            self._db.direction_results[new_id] = {
                "Id": new_id,
                "SessionId": session_id,
                "Direction": direction,
                "AttemptNumber": attempt_number,
                "Verdict": verdict,
                "CreatedAt": created_at,
            }
            self._db._last_identity = new_id
        elif sql_norm.startswith("SELECT @@IDENTITY"):
            self._last_result = [(self._db._last_identity,)]
        elif sql_norm.startswith("INSERT INTO CHECKRESULTS"):
            direction_result_id, check_type, measured, threshold, status = params
            self._db.check_id_seq += 1
            self._db.check_results.append(
                {
                    "Id": self._db.check_id_seq,
                    "DirectionResultId": direction_result_id,
                    "CheckType": check_type,
                    "MeasuredValue": measured,
                    "ThresholdUsed": threshold,
                    "Status": status,
                }
            )
        elif sql_norm.startswith("SELECT SESSIONID, SCOPEID, OPERATOR"):
            (scope_id,) = params
            rows = [s for s in self._db.sessions.values() if s["ScopeId"] == scope_id]
            rows.sort(key=lambda r: r["StartedAt"], reverse=True)
            self.description = [(c,) for c in ["SessionId", "ScopeId", "Operator", "StartedAt", "CompletedAt", "OverallVerdict"]]
            self._last_result = [
                (r["SessionId"], r["ScopeId"], r["Operator"], r["StartedAt"], r["CompletedAt"], r["OverallVerdict"])
                for r in rows
            ]
        else:
            raise NotImplementedError(f"FakeCursor가 처리하지 못하는 SQL: {sql_norm[:80]}")

    def fetchone(self):
        return self._last_result[0] if self._last_result else None

    def fetchall(self):
        return list(self._last_result)


class FakeMssqlConnection:
    def __init__(self) -> None:
        self.scopes: dict[str, str] = {}
        self.sessions: dict[str, dict] = {}
        self.direction_results: dict[int, dict] = {}
        self.check_results: list[dict] = []
        self.direction_id_seq = 0
        self.check_id_seq = 0
        self._last_identity = 0
        self.commit_count = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commit_count += 1
