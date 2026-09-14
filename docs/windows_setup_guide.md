# Windows 환경 설치 및 실행 가이드

이 프로그램은 Windows에서 실행하는 것을 기본으로 설계되었습니다 (IDS peak SDK, 산업용 카메라
연동 모두 Windows를 우선 지원). 아래 순서대로 진행하면 됩니다.

이 문서는 "코드를 받아서 `python -m app.main`으로 실행/테스트"하는 단계까지를 다룹니다.
현장 PC에 파이썬 없이 더블클릭으로 실행되는 `.exe`를 만드는 **빌드(배포용 패키징)**는 별도
문서 `docs/packaging_guide.md`에 있습니다 - 파이썬 자체는 컴파일이 필요 없는 인터프리터
언어라 개발 중에는 빌드 과정이 없고(그래서 이 문서에는 "빌드" 항목이 없습니다), 배포용
`.exe`를 만들 때만 PyInstaller로 파이썬 런타임을 통째로 묶어 패키징합니다. 이 문서를 끝까지
따라 `python -m app.main --mock`이 정상 동작하는 걸 확인한 뒤, 배포가 필요하면
`packaging_guide.md`로 넘어가세요.

## 0. (선택) 가상머신(VM)에서 작업하기

당장 손에 Windows PC가 없거나(예: Mac/Linux 개발 환경), 실제 현장 PC를 건드리지 않고
먼저 격리된 환경에서 검증해보고 싶다면 Windows 가상머신(VM)에서 이 문서를 그대로 따라갈 수
있습니다. VM 생성은 다음 중 하나를 사용하세요:

- **Hyper-V** (Windows 10/11 Pro·Enterprise에 내장, 무료) - 호스트가 이미 Windows일 때 가장
  간단합니다. 설정 > 앱 > Windows 기능 켜기/끄기에서 "Hyper-V" 체크 후 재부팅, Hyper-V
  관리자에서 새 가상 머신을 만들고 Windows 설치 ISO를 연결합니다.
- **VirtualBox**(무료, Oracle) 또는 **VMware Workstation Player**(개인/비상업 용도 무료) -
  호스트가 Mac/Linux거나 Hyper-V를 쓸 수 없을 때. 둘 다 USB 장치를 VM 안으로 전달(USB
  패스스루)하는 기능이 Hyper-V보다 다루기 쉽습니다.
- Windows 평가판 ISO는 Microsoft 공식 페이지(예: Windows 11 Enterprise 평가판)에서 받을 수
  있습니다 - 90일 등 평가 기간 동안 라이선스 없이 테스트 가능.

**VM으로 확인 가능한 것 / 불가능한 것**:
- 가능: 파이썬 설치, `pip install`, `--mock`/`--playback` 모드로 화면·검사 흐름 확인,
  `pytest` 전체 테스트, PyInstaller로 `.exe` 빌드까지 - 이 문서와 `packaging_guide.md`의
  대부분 단계는 VM만으로 충분합니다.
- 어려움/권장하지 않음: **실제 IDS 카메라 연동 테스트**. USB 카메라를 VM에 패스스루할 수는
  있지만(VirtualBox/VMware 기준) 대역폭/지연/드라이버 인식 문제로 불안정한 경우가 많습니다.
  카메라가 실제로 잘 잡히는지, 영상이 끊기지 않는지는 **반드시 실제(베어메탈) Windows
  PC**에서 최종 확인하세요. MSSQL 연결(6번 항목)은 네트워크로 붙는 것이라 VM에서도
  문제없이 테스트할 수 있습니다.

**VS Code + Claude Code로 VM 안에서 직접 수정하며 테스트하려면**: VM의 Windows에 VS Code
(https://code.visualstudio.com/)를 설치하고, 확장(Extensions) 탭에서 "Claude Code"를 검색해
설치한 뒤 그 안에서 로그인하면 됩니다. 이 VM이 곧 "작업 PC"가 되는 것이므로, 이 문서의
1~8번(파이썬 설치 ~ 테스트 실행)을 VS Code의 통합 터미널(터미널 > 새 터미널)에서 그대로
따라가면 됩니다 - VM 밖 호스트 PC에서 따로 할 일은 없습니다.

VM 준비가 끝났으면(또는 이미 Windows PC가 있다면) 아래 1번부터 그대로 진행하면 됩니다.

## 1. Python 설치

1. Python 3.12는 정규 유지보수 지원이 종료되어 최신 3.12.x 버전은 Windows용 설치
   프로그램(.exe)이 제공되지 않습니다(소스 코드만 배포) - 아래 중 하나를 받으세요:
   - **Python 3.12.10**(Windows 설치파일이 제공되는 마지막 3.12 버전):
     https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
   - **Python 3.13.x**(현재 정식 지원 중인 최신 안정 버전, 장기적으로 권장):
     https://www.python.org/downloads/ 에서 최신 3.13 버전 다운로드
2. 설치 화면에서 **"Add python.exe to PATH"** 체크박스를 반드시 체크하세요.
3. 설치 후 명령 프롬프트(cmd)나 PowerShell을 새로 열고 확인:
   ```
   python --version
   ```

### 1-1. ⚠️ 아나콘다(conda)를 함께 쓰는 PC라면 반드시 읽어주세요

conda가 설치되어 있으면 PowerShell을 열 때마다 `(base)`가 자동으로 활성화되고, conda
자체의 hook이 매 명령마다 PATH 맨 앞에 자신의 경로를 다시 끼워 넣습니다. 이 때문에
`.venv`를 활성화해 화면에 `(.venv)`가 떠 있어도, 실제로는 `python` 명령이 조용히 conda의
Python으로 실행되는 경우가 있습니다. 이 상태로 `.venv`를 만들면 `.venv` 자체가 conda의
Python을 기반으로 만들어져 버려서, 이후 패키지 설치/실행 중 원인 불명의 DLL 오류
(`DLL 초기화 루틴을 실행할 수 없습니다` 등)가 발생할 수 있습니다.

**권장 조치**:
```
conda config --set auto_activate_base false
```
이 프로젝트에서 conda를 쓸 계획이 없다면, 아예 제거하는 것도 좋은 방법입니다(설정 → 앱 →
Anaconda3 제거). 제거 후에는 새 터미널에서 `where.exe python`으로 conda 경로가 더 안
뜨는지 확인하세요.

가상환경(3번 항목)을 만들기 전에는 항상 아래로 실제 어떤 Python이 쓰이는지 확인하는
습관을 들이면 좋습니다:
```
where.exe python
python -c "import sys; print(sys.executable)"
```
(PowerShell의 `where`는 `Where-Object`의 별칭이라 다르게 동작하므로, 반드시 `where.exe`로
실행하세요.)

## 2. 프로젝트 코드 받기

Git이 설치되어 있다면:
```
git clone <이 저장소 주소>
cd scope
```
Git이 없다면 GitHub에서 ZIP으로 다운로드해 압축을 풀고 해당 폴더로 이동하세요.

### 2-1. 코드를 수정한 뒤 다시 커밋/push하려면

GitHub에 push할 수 있는 계정으로 최초 1회 Git 인증만 설정해두면(SSH 키 등록 또는
`gh auth login`), 이후에는 매번 권한을 새로 받을 필요 없이 바로 커밋/push할 수 있습니다.

수정 -> 테스트 -> 커밋 -> push를 매번 손으로 하기 번거롭다면 저장소에 포함된 스크립트를
쓰세요 - 변경사항을 보여주고, 테스트를 자동 실행해 실패하면 커밋을 막고, 커밋 전/push 전
각각 확인을 받습니다:
```
powershell -ExecutionPolicy Bypass -File scripts\commit_and_push.ps1
```
(WSL/Linux/Mac에서는 `./scripts/commit_and_push.sh` - 최초 1회 `chmod +x` 필요)

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
### 5-3. Python 바인딩 설치는 wheel 대신 PyPI로 (권장)

과거에는 IDS Software Suite 설치 폴더 안의 wheel 파일을 직접 `pip install`해야 했지만,
IDS가 공식적으로 PyPI에 배포하는 패키지를 쓰는 것이 훨씬 간단합니다 - 경로를 찾아다닐
필요 없이 일반 패키지처럼 설치됩니다:
```
pip install ids-peak
pip install ids-peak-ipl
```

설치 확인:
```
python -c "import ids_peak_ipl; import ids_peak; print('OK')"
```
버전 확인은 `pip show ids-peak-ipl`로 할 수 있습니다.

### 5-4. API 이름 변경 주의 (ids_peak_ipl)

PyPI로 설치한 최신 버전의 `ids_peak_ipl`에서는 이미지 생성 함수가 평평한 함수 이름이
아니라 **클래스 메서드 방식**으로 바뀌었습니다:
```python
# 예전 방식 (동작 안 함) - AttributeError 발생
ids_peak_ipl.Image_CreateFromSizeAndBuffer(pixel_format, base_ptr, size, width, height)

# 현재 방식
ids_peak_ipl.Image.CreateFromSizeAndBuffer(pixel_format, base_ptr, size, width, height)
```
이 저장소의 `core/camera/ids_peak_camera_service.py`는 이미 현재 방식(`ipl.Image.
CreateFromSizeAndBuffer(...)`)으로 되어 있습니다. 만약 SDK를 더 옛 버전으로 설치했거나
다른 예제 코드를 참고해 옛 방식(`Image_CreateFromSizeAndBuffer`)의 호출부를 추가했다면
`AttributeError`가 나므로, 클래스 메서드 방식으로 고쳐야 합니다.

### 5-5. 노드명(GenApi) 확인

`core/camera/ids_peak_camera_service.py` 안의 노드명(`ExposureTime`, `Gain` 등)은 계획 단계의
추정치이므로, 실제 카메라에서 IDS peak Cockpit이나 코드로 노드맵을 확인해 다를 경우 이름을
맞춰줘야 할 수 있습니다 (콘솔에 "설정 실패(노드명 확인 필요)" 로그가 뜨면 해당 항목입니다).

### 5-6. 이 카메라(U3-388xLE-C)의 실제 제약사항 (하드웨어 자체의 한계, 코드로 못 고침)

실기로 노드맵을 확인한 결과, 아래 기능은 이 카메라 모델에 애초에 없는 기능입니다 - 코드
문제가 아니라 하드웨어 사양이므로 콘솔에 "설정 실패(노드명 확인 필요)" 로그가 남는 것이
정상입니다:

- `ExposureAuto` / `GainAuto` / `BalanceWhiteAuto`: 카메라 자체 오토 기능 없음 - 항상 조용히
  건너뜀 (정상 동작이며, 애초에 `settings.json`에서 이 세 항목은 `"off"` 고정 사용을 권장).
- 화이트밸런스 R/G/B 게인(`BalanceRatio` 노드): 카메라에 이 노드가 없음.

**화이트밸런스는 호스트 측(소프트웨어)에서 구현되어 있습니다** - `BalanceRatio` 노드가
없다는 것을 확인한 뒤, `ids_peak_ipl.Gain` 클래스(IDS SDK가 공식 제공하는 이미지 처리
라이브러리의 클래스, 카메라 하드웨어와 무관하게 동작)로 캡처된 각 프레임에 직접 R/G/B
게인을 곱하는 방식으로 `core/camera/ids_peak_camera_service.py`(`apply_settings()`,
`_buffer_to_bgr()`)에 이미 구현되어 있습니다:
```python
gain = ids_peak_ipl.Gain()
gain.SetRedGainValue(settings.wb_gain_r)
gain.SetGreenGainValue(settings.wb_gain_g)
gain.SetBlueGainValue(settings.wb_gain_b)
gain.ProcessInPlace(raw_image)  # 컬러 변환(BGR8) 전, Bayer/Mono 원본 단계에서 적용
```
(실제 `ids_peak_ipl` 네이티브 라이브러리로 합성 Bayer 패턴 이미지를 만들어 R 게인만 2배로
줬을 때 R 채널 픽셀만 정확히 2배가 되고 G/B는 그대로인 것을 직접 확인함 - 참고 구현체:
https://github.com/ids-imaging/ids-peak-examples 의 `python/gui_kivy_pipeline`이 같은
`ids_peak_ipl.Gain` 클래스를 자동 화이트밸런스 파이프라인의 최종 적용 단계로 사용합니다.
IDS가 별도 배포하는 `ids-peak-afl`(Auto-Feature Library) 패키지는 이 게인 값을 자동으로
계산해주는 오토 화이트밸런스/오토 밝기 기능을 제공하지만, 이 프로젝트는 수동으로 지정한
`wb_gain_r/g/b` 값만 쓰므로 `ids-peak-afl` 설치는 필요 없습니다.)

`settings.json`에서 `camera.wb_gain_r/g/b`를 조정하면(예: 1.0 = 변화 없음, 1.2 = 20% 증가)
다음 프레임부터 바로 반영됩니다 - 카메라 재시작이나 재연결이 필요 없습니다.

⚠️ **유효 범위는 1.0~8.0입니다(1.0 미만 불가)** - `ids_peak_ipl.Gain`은 게인을 곱하기만
하고 나눌 수는 없어서, 특정 채널을 "덜 붉게/덜 파랗게" 만들고 싶다면 그 채널 값을 낮추는
게 아니라 **나머지 채널들을 올려서** 상대적으로 맞춰야 합니다. 범위를 벗어난 값을 넣으면
`IdsPeakCameraService`가 자동으로 1.0~8.0 범위로 조정(clamp)하고 콘솔에 로그를 남깁니다.

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
테스트가 모두 통과해야 합니다. 하나라도 실패하면 어떤 부분이 왜 실패했는지 알려주세요.

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

## 9. 배포용 실행파일(.exe) 빌드 (현장 PC 배포 시)

여기까지는 "이 PC에 파이썬이 설치되어 있어야" 실행 가능한 상태입니다. 검사 장비가 설치될
현장 PC마다 파이썬/의존성을 똑같이 설치하는 대신, 더블클릭 한 번으로 실행되는 단일 배포
폴더(.exe 포함)를 만들 수 있습니다 - **PyInstaller**로 지금 이 파이썬 환경을 통째로 굳혀서
포장하는 방식입니다. 자세한 절차(빌드 실행, 결과물 구성, 현장 배포 체크리스트, 자주 발생하는
문제)는 `docs/packaging_guide.md`를 참고하세요. 요지만 정리하면:

```
pip install pyinstaller
pyinstaller packaging\scope_inspector.spec
dist\ScopeInspector\ScopeInspector.exe --mock
```

빌드는 **실제 배포 대상과 동일한 Windows 환경**(VM으로 진행했다면 그 VM, 또는 대상 PC와
같은 아키텍처의 다른 Windows PC)에서 해야 합니다 - 이 PC에서 돌던 환경을 그대로 굳히는
작업이라 다른 OS/아키텍처에서 빌드한 결과물은 대상 PC에서 그대로 동작하지 않습니다.

## 10. VS Code + Claude Code 설치 (선택)

VM 안내(0번 항목)에서 소개한 방식을 로컬 PC에도 그대로 적용할 수 있습니다 - 코드를 직접
보면서 Claude와 대화하며 수정/테스트하고 싶다면:

1. https://code.visualstudio.com/ 에서 VS Code를 설치합니다.
2. VS Code에서 **File > Open Folder**로 이 프로젝트 폴더(`scope`)를 엽니다.
3. 좌측 확장(Extensions) 탭에서 **"Claude Code"**를 검색해 설치합니다.
4. 설치된 Claude 아이콘을 클릭하고, 열리는 브라우저 창에서 계정으로 로그인합니다.
5. 이후 코드 관련 질문이나 수정 요청을 채팅창에서 바로 진행할 수 있습니다(터미널 명령
   실행, 파일 수정, 테스트 실행 등을 직접 대신 해줄 수 있습니다).

## 자주 발생하는 문제

| 증상 | 원인/해결 |
|---|---|
| `python`이 인식되지 않음 | 설치 시 PATH 추가를 체크하지 않았을 가능성 - Python 재설치 시 체크 |
| `ModuleNotFoundError: No module named 'ids_peak'` | IDS peak SDK Python 패키지가 설치 안 됨 - 5번 항목 참고, `--mock`/`--playback`으로 우선 진행 가능 |
| `pyodbc.Error`로 DB 연결 실패 | ODBC Driver 미설치, 서버 주소/계정 오류, 방화벽/VPN 문제 확인 |
| 카메라 화면이 안 나옴 | IDS peak Cockpit에서 먼저 카메라가 잡히는지 확인 (드라이버/USB 문제 여부 판단) |
| 화면이 너무 작게/크게 나옴 | 아직 세부 UI 레이아웃은 다듬는 중 - 창 크기를 직접 조절하거나 알려주시면 개선하겠습니다 |
| `ImportError: DLL load failed while importing _ids_peak_ipl_python_interface: DLL 초기화 루틴을 실행할 수 없습니다` | 대부분 conda와의 PATH 충돌이 원인 - `.venv`가 실제로는 conda의 Python을 참조하고 있을 수 있음(1-1번 항목 참고). `where.exe python`으로 실제 경로 확인 후, conda 비활성화(`conda deactivate`) 또는 완전 제거 후 `.venv`를 처음부터 다시 생성 |
| `module 'ids_peak_ipl.ids_peak_ipl' has no attribute 'Image_CreateFromSizeAndBuffer'` | 최신 `ids_peak_ipl` 버전에서는 `Image_CreateFromSizeAndBuffer` → `Image.CreateFromSizeAndBuffer`로 API 이름이 변경됨(5-4번 항목 참고) - 코드에서 해당 호출부를 클래스 메서드 방식으로 수정 |
| `where python`을 쳤는데 아무것도 안 뜸 | PowerShell에서는 `where`가 `Where-Object`의 별칭이라 다르게 동작함 - `where.exe python`으로 실행할 것 |
