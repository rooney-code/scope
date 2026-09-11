# Windows 환경 설치 및 실행 가이드

이 프로그램은 Windows에서 실행하는 것을 기본으로 설계되었습니다 (IDS peak SDK, 산업용 카메라
연동 모두 Windows를 우선 지원). 아래 순서대로 진행하면 됩니다.

## 1. Python 설치

1. https://www.python.org/downloads/ 에서 **Python 3.10 이상** 설치 프로그램을 받습니다
   (3.11 또는 3.12 권장).
2. 설치 화면에서 **"Add python.exe to PATH"** 체크박스를 반드시 체크하세요.
3. 설치 후 명령 프롬프트(cmd)나 PowerShell을 새로 열고 확인:
   ```
   python --version
   ```

## 2. 프로젝트 코드 받기

Git이 설치되어 있다면:
```
git clone <이 저장소 주소>
cd scope
```
Git이 없다면 GitHub에서 ZIP으로 다운로드해 압축을 풀고 해당 폴더로 이동하세요.

## 3. 가상환경 생성 (권장)

```
python -m venv .venv
.venv\Scripts\activate
```
프롬프트 앞에 `(.venv)`가 표시되면 활성화된 것입니다. 이후 모든 명령은 이 가상환경 안에서 실행합니다.

## 4. 파이썬 패키지 설치

```
pip install -r requirements.txt
```
여기까지 하면 `PySide6`(화면), `opencv-python`(영상처리), `numpy`, `pyodbc`(DB 연결)가 설치됩니다.

### 4-1. 카메라 없이 먼저 테스트하고 싶다면

바로 5번(실행)으로 넘어가서 `--mock` 옵션으로 실행해도 됩니다. 실제 카메라나 DB 없이도
화면과 검사 흐름을 확인할 수 있습니다.

## 5. IDS 카메라 연동 준비 (실제 카메라 사용 시)

1. IDS 공식 홈페이지(https://en.ids-imaging.com/ )에서 **IDS Software Suite**(또는 IDS peak)를
   내려받아 설치합니다. 이 설치 과정에서 카메라 드라이버도 함께 설치됩니다.
2. 카메라(USB3)를 PC에 연결하고, IDS에서 제공하는 **IDS peak Cockpit** 프로그램으로 카메라가
   정상적으로 인식되고 영상이 나오는지 먼저 확인하세요 (이 프로그램과 별개로, 하드웨어/드라이버
   문제인지 먼저 가려내기 위함입니다).
3. IDS peak SDK의 Python 바인딩(`ids_peak`, `ids_peak_ipl`)을 설치합니다. 설치 방법은 IDS
   Software Suite에 포함된 예제/문서의 안내를 따르세요 (보통 설치 폴더 안의 wheel 파일을
   `pip install`하는 방식입니다). 예:
   ```
   pip install "C:\Program Files\IDS\ids_peak\...\ids_peak-X.Y.Z-cp311-cp311-win_amd64.whl"
   ```
   (정확한 경로/파일명은 설치된 IDS Software Suite 버전에 따라 다릅니다.)
4. `core/camera/ids_peak_camera_service.py` 안의 노드명(`ExposureTime`, `Gain` 등)은 계획 단계의
   추정치이므로, 실제 카메라에서 IDS peak Cockpit이나 코드로 노드맵을 확인해 다를 경우 이름을
   맞춰줘야 할 수 있습니다 (콘솔에 "설정 실패(노드명 확인 필요)" 로그가 뜨면 해당 항목입니다).

## 6. MSSQL 연결 준비 (결과 저장 사용 시)

1. Microsoft 공식 페이지에서 **ODBC Driver 18 for SQL Server**를 설치합니다.
   (https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server)
2. 회사 MSSQL 서버 주소/DB명/인증 정보를 알아둡니다.
3. `core/data/schema.sql`을 SQL Server Management Studio(SSMS) 또는 `sqlcmd`로 대상 데이터베이스에
   한 번 실행해 테이블을 만들어 둡니다.
4. `settings.json`의 `database` 항목을 채웁니다:
   ```json
   "database": {
     "server": "내부서버주소",
     "database": "DB이름",
     "auth_mode": "sql",
     "user": "계정",
     "password": "비밀번호"
   }
   ```
   비밀번호를 파일에 평문으로 두는 게 꺼려지면, `password`는 빈 값으로 두고 환경변수 등으로
   따로 주입하도록 추후 보완할 수 있습니다(현재는 TBD로 설정 파일에 자리만 마련되어 있습니다).

## 7. 실행

가상환경이 활성화된 상태(`(.venv)` 표시)에서, 프로젝트 최상위 폴더에서 실행합니다.

- **카메라/DB 없이 화면 흐름만 확인** (가장 먼저 이걸로 테스트하는 것을 권장):
  ```
  python -m app.main --mock
  ```
- **실제 캡처한 이미지/영상 파일로 재생 테스트** (카메라 없이, 실제 자료로 검출 정확도 확인):
  ```
  python -m app.main --playback "C:\경로\밝은화면.png"
  python -m app.main --playback "C:\경로\시험영상.mp4"
  ```
- **실제 IDS 카메라로 실행**:
  ```
  python -m app.main
  ```
  카메라 시리얼을 지정해야 하면:
  ```
  python -m app.main --device-serial 1234567890
  ```

## 8. 테스트 코드 실행 (로직 검증)

프로젝트에는 하드웨어 없이도 돌려볼 수 있는 자동 테스트가 포함되어 있습니다.
```
pip install pytest
pytest -q
```
28개 테스트가 모두 통과해야 합니다. 하나라도 실패하면 어떤 부분이 왜 실패했는지 알려주세요.

추가로, 실제 UI 배선까지 한 번에 확인해보는 스모크 테스트도 있습니다(카메라 창은 화면에 안
뜨는 offscreen 모드로 실행):
```
set QT_QPA_PLATFORM=offscreen
set PYTHONPATH=.
python scripts\smoke_test_mock_ui.py
```
"스모크 테스트 통과"가 출력되면 정상입니다.

화면을 실제로 보면서 확인하려면 `QT_QPA_PLATFORM` 환경변수를 설정하지 말고 그냥
```
python -m app.main --mock
```
로 실행하면 됩니다.

## 자주 발생하는 문제

| 증상 | 원인/해결 |
|---|---|
| `python`이 인식되지 않음 | 설치 시 PATH 추가를 체크하지 않았을 가능성 - Python 재설치 시 체크 |
| `ModuleNotFoundError: No module named 'ids_peak'` | IDS peak SDK Python 패키지가 설치 안 됨 - 5번 항목 참고, `--mock`/`--playback`으로 우선 진행 가능 |
| `pyodbc.Error`로 DB 연결 실패 | ODBC Driver 미설치, 서버 주소/계정 오류, 방화벽/VPN 문제 확인 |
| 카메라 화면이 안 나옴 | IDS peak Cockpit에서 먼저 카메라가 잡히는지 확인 (드라이버/USB 문제 여부 판단) |
| 화면이 너무 작게/크게 나옴 | 아직 세부 UI 레이아웃은 다듬는 중 - 창 크기를 직접 조절하거나 알려주시면 개선하겠습니다 |
