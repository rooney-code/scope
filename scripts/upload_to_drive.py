"""로컬에서 캡처한 샘플 이미지를 구글 드라이브 폴더로 업로드하는 스크립트.

사전 준비 (최초 1회):
1. https://console.cloud.google.com/ 에서 프로젝트 생성(또는 기존 프로젝트 선택).
2. "API 및 서비스 > 라이브러리"에서 "Google Drive API" 활성화.
3. "API 및 서비스 > 사용자 인증 정보"에서 "OAuth 클라이언트 ID" 생성
   - 애플리케이션 유형: "데스크톱 앱"
   - 다운로드한 JSON을 이 스크립트와 같은 폴더에 credentials.json 이름으로 저장.
4. 패키지 설치:
   pip install google-auth-oauthlib google-api-python-client google-auth-httplib2

실행:
   python upload_to_drive.py 이미지1.jpg 이미지2.jpg ...
   또는
   python upload_to_drive.py 폴더경로/   (폴더 안의 이미지 파일을 전부 업로드)

처음 실행 시 브라우저가 열려 구글 계정 로그인/권한 동의를 요청한다(본인 드라이브에만
접근하는 최소 권한 - drive.file 스코프, 이 앱으로 만들거나 연 파일만 접근 가능).
이후에는 token.json에 저장된 인증정보를 재사용해 다시 로그인할 필요가 없다.
"""
from __future__ import annotations

import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# 업로드 대상 폴더 ID - "조준경_검사_샘플이미지" (Claude가 생성)
# https://drive.google.com/drive/folders/1wZq2EgiS64UOLZuf3wj7HJQeOXaZVZlM
FOLDER_ID = "1wZq2EgiS64UOLZuf3wj7HJQeOXaZVZlM"

# drive.file: 이 앱이 만들었거나 사용자가 이 앱으로 연 파일에만 접근하는 최소 권한 스코프
# (드라이브 전체를 읽고 쓰는 광범위한 권한을 요청하지 않음)
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

SCRIPT_DIR = Path(__file__).resolve().parent
CREDENTIALS_PATH = SCRIPT_DIR / "credentials.json"
TOKEN_PATH = SCRIPT_DIR / "token.json"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _get_credentials() -> Credentials:
    creds: Credentials | None = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_PATH.exists():
                raise SystemExit(
                    f"credentials.json이 없습니다: {CREDENTIALS_PATH}\n"
                    "스크립트 상단 주석의 사전 준비 단계를 먼저 진행하세요."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)  # 브라우저가 열림(최초 1회)
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    return creds


def _collect_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            files.extend(sorted(f for f in path.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS))
        elif path.is_file():
            files.append(path)
        else:
            print(f"건너뜀 (파일/폴더 없음): {p}")
    return files


def upload_files(paths: list[str], folder_id: str = FOLDER_ID) -> None:
    creds = _get_credentials()
    service = build("drive", "v3", credentials=creds)

    files = _collect_files(paths)
    if not files:
        print("업로드할 이미지가 없습니다.")
        return

    for file_path in files:
        mime_type = _MIME_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
        media = MediaFileUpload(str(file_path), mimetype=mime_type, resumable=True)
        metadata = {"name": file_path.name, "parents": [folder_id]}
        uploaded = service.files().create(body=metadata, media_body=media, fields="id, name, webViewLink").execute()
        print(f"업로드 완료: {uploaded['name']} -> {uploaded.get('webViewLink', uploaded['id'])}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    upload_files(sys.argv[1:])
