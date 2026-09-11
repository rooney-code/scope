# 배포용 실행 파일(.exe) 만들기 가이드

파이썬이 설치되지 않은 검사 PC에서도 더블클릭으로 실행할 수 있는 단일 실행 파일을 만드는
방법입니다. **PyInstaller**(오픈소스, GPL 계열 아님 - 상용 배포에 무료로 사용 가능)를 사용합니다.

이 작업은 실제 카메라/DB가 연결된 대상 PC(또는 그와 동일한 Windows 환경)에서 진행해야 합니다.
패키징은 "그 PC에서 돌던 파이썬 환경을 그대로 굳혀서 exe로 포장하는" 작업이라, 다른 OS/아키텍처
에서 빌드한 exe는 대상 PC에서 그대로 동작하지 않습니다.

## 0. 전제 조건

- `docs/windows_setup_guide.md`를 따라 이미 `python -m app.main`이 정상 동작하는 상태여야 합니다
  (카메라/DB 연동 여부와 무관하게, 최소한 `--mock` 모드가 실행되는 것까지는 확인).
- IDS peak SDK, ODBC Driver 등 **시스템 레벨 드라이버/런타임은 exe 안에 포함되지 않습니다.**
  대상 PC마다 이 드라이버들은 별도로 설치되어 있어야 합니다 (일반 사용자용 설치 프로그램을
  따로 준비하거나, IT 배포 이미지에 미리 포함해두는 것을 권장합니다).

## 1. PyInstaller 설치

가상환경(`.venv`) 활성화 상태에서:
```
pip install pyinstaller
```

## 2. 빌드용 spec 파일 (이미 포함됨)

저장소에 `packaging/scope_inspector.spec`을 준비해두었습니다. 데이터 파일(`settings.json`,
`core/data/schema.sql`, `docs/`)과 PySide6 플러그인을 함께 포함하도록 설정되어 있습니다.

## 3. 빌드 실행

프로젝트 최상위 폴더에서:
```
pyinstaller packaging\scope_inspector.spec
```
빌드가 끝나면 `dist\ScopeInspector\` 폴더에 `ScopeInspector.exe`와 필요한 DLL/리소스들이
함께 생성됩니다. **이 `dist\ScopeInspector\` 폴더 전체를 배포**해야 합니다 - exe 파일 하나만
복사하면 동작하지 않습니다.

(참고: `--onefile` 단일 exe 방식도 가능하지만, 실행 시마다 임시폴더에 압축을 풀어 시작이
느려지고 바이러스 백신이 오탐하는 경우가 많아 산업 현장 배포에는 폴더형(`--onedir`, 기본값이자
spec에 적용된 방식)을 권장합니다.)

## 4. 빌드 결과 테스트

빌드한 PC에서 바로 확인:
```
dist\ScopeInspector\ScopeInspector.exe --mock
```
정상적으로 창이 뜨면 성공입니다. 이후 실제 카메라가 연결된 PC로 `dist\ScopeInspector\` 폴더
전체를 복사해 옮긴 뒤, 그 PC에서도 한 번 더 실행해 확인하세요 (다른 PC에서는 IDS peak
드라이버가 없으면 실제 카메라 모드가 실패할 수 있습니다 - 이는 exe 문제가 아니라 드라이버
설치 여부 문제입니다).

## 5. 설정 파일(settings.json)은 exe 밖에 둔다

`ScopeInspector.exe`가 있는 폴더 옆에 `settings.json`을 두면 그 파일을 읽습니다. 즉:
```
dist\ScopeInspector\
  ScopeInspector.exe
  settings.json          <- 카메라/DB 설정을 현장에 맞게 이 파일에서 직접 수정
  core\data\schema.sql
  ...
```
검사 장비(카메라)가 여러 대라면, 각 PC의 `settings.json`에서 `camera.device_serial`과
`database.*` 값만 다르게 맞춰주면 됩니다 - 코드를 다시 빌드할 필요는 없습니다.

## 6. 배포 체크리스트 (현장 PC마다)

- [ ] IDS Software Suite(카메라 드라이버, IDS peak 런타임) 설치됨
- [ ] Microsoft ODBC Driver 18 for SQL Server 설치됨
- [ ] `dist\ScopeInspector\` 폴더 전체가 복사되어 있음
- [ ] `settings.json`의 `camera.device_serial`, `database.*`가 그 PC/장비에 맞게 채워져 있음
- [ ] `core\data\schema.sql`이 대상 MSSQL 데이터베이스에 이미 실행되어 테이블이 만들어져 있음
- [ ] `ScopeInspector.exe --mock`으로 화면이 뜨는지 먼저 확인 후, 카메라 연결 상태에서 실제 실행

## 7. 자주 발생하는 문제

| 증상 | 원인/해결 |
|---|---|
| exe 실행 시 바로 창이 사라짐(콘솔 창도 없음) | `packaging\scope_inspector.spec`에서 `console=True`로 바꿔 다시 빌드하면 오류 메시지를 콘솔에서 볼 수 있음 |
| `Failed to execute script` 오류 | 콘솔 모드로 재빌드해 정확한 파이썬 예외 메시지 확인 필요 - 보통 숨은 임포트(hidden import) 누락 |
| 다른 PC로 옮기면 실행이 안 됨 | `dist\ScopeInspector\` 폴더를 통째로 복사했는지 확인 (exe 파일만 복사하면 안 됨) |
| 카메라 모드만 실패 | IDS 드라이버 설치 여부 확인 - `--mock`은 되는데 실카메라만 안 되면 exe 문제가 아님 |
| 바이러스 백신이 exe를 차단/삭제함 | PyInstaller로 만든 실행 파일은 오탐되는 경우가 흔함 - 사내 백신 정책에 예외 등록 필요 |

## 8. 버전 관리

배포할 때마다 `app/main.py`나 창 제목에 버전 문자열을 넣어두면(예: "v0.1.0"), 현장에서 여러
버전이 섞였을 때 어떤 빌드인지 구분하기 쉬워집니다. 필요하시면 다음 빌드에 반영해 드리겠습니다.
