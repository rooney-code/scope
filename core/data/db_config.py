"""MSSQL 연결 문자열/연결 생성.

pyodbc는 지연 임포트한다 - unixODBC/드라이버가 없는 개발 환경(예: 이 저장소의 CI/샌드박스)에서도
나머지 코드가 정상적으로 임포트/테스트 가능해야 하기 때문이다.
"""
from __future__ import annotations

from core.config.settings import DatabaseSettings


class PyodbcNotAvailableError(RuntimeError):
    """pyodbc 또는 Microsoft ODBC Driver가 설치되어 있지 않을 때 발생."""


def build_connection_string(settings: DatabaseSettings, driver: str = "ODBC Driver 18 for SQL Server") -> str:
    if not settings.server or not settings.database:
        raise ValueError("database.server / database.database 설정이 필요합니다.")

    if settings.auth_mode == "windows":
        return (
            f"DRIVER={{{driver}}};SERVER={settings.server};DATABASE={settings.database};"
            "Trusted_Connection=yes;"
        )

    if not settings.user:
        raise ValueError("SQL 인증 모드에서는 database.user가 필요합니다 (password는 환경변수 권장).")
    return (
        f"DRIVER={{{driver}}};SERVER={settings.server};DATABASE={settings.database};"
        f"UID={settings.user};PWD={settings.password};"
    )


def connect(settings: DatabaseSettings):
    try:
        import pyodbc  # type: ignore
    except ImportError as exc:
        raise PyodbcNotAvailableError(
            "pyodbc를 임포트할 수 없습니다. pyodbc 패키지와 Microsoft ODBC Driver for SQL Server가 "
            "설치된 환경에서 실행하세요."
        ) from exc

    conn_str = build_connection_string(settings)
    return pyodbc.connect(conn_str)
