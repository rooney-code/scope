-- MSSQL(T-SQL) 스키마 - 계획 문서(logical-herding-teapot.md) 참고
-- 실행 전: 대상 데이터베이스를 선택(USE) 한 상태에서 실행할 것.

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Scopes')
BEGIN
    CREATE TABLE Scopes (
        ScopeId   NVARCHAR(50) NOT NULL PRIMARY KEY,
        CreatedAt DATETIME2    NOT NULL DEFAULT SYSUTCDATETIME()
    );
END;

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'InspectionSessions')
BEGIN
    CREATE TABLE InspectionSessions (
        SessionId               UNIQUEIDENTIFIER NOT NULL PRIMARY KEY DEFAULT NEWID(),
        ScopeId                  NVARCHAR(50)     NOT NULL REFERENCES Scopes(ScopeId),
        Operator                 NVARCHAR(100)    NOT NULL,
        StartedAt                DATETIME2        NOT NULL,
        CompletedAt              DATETIME2        NULL,
        OverallVerdict           NVARCHAR(20)     NULL,   -- '합격' | '불량' | '진행중'
        CalibrationSnapshotJson  NVARCHAR(MAX)    NULL
    );
END;

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'DirectionResults')
BEGIN
    CREATE TABLE DirectionResults (
        Id             INT IDENTITY(1,1) PRIMARY KEY,
        SessionId      UNIQUEIDENTIFIER NOT NULL REFERENCES InspectionSessions(SessionId),
        Direction      NVARCHAR(10)     NOT NULL,   -- Up|Down|Left|Right
        AttemptNumber  INT              NOT NULL,
        Verdict        NVARCHAR(10)     NOT NULL,   -- 합격 | 불량
        CreatedAt      DATETIME2        NOT NULL DEFAULT SYSUTCDATETIME()
    );
END;

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'CheckResults')
BEGIN
    CREATE TABLE CheckResults (
        Id                  INT IDENTITY(1,1) PRIMARY KEY,
        DirectionResultId   INT           NOT NULL REFERENCES DirectionResults(Id),
        CheckType           NVARCHAR(20)  NOT NULL,  -- TravelAmount|DeadClick|Drift|Shift|Backlash
        MeasuredValue        FLOAT        NULL,
        ThresholdUsed        FLOAT        NULL,
        Status               NVARCHAR(10) NOT NULL   -- 합격 | 불량
    );
END;
