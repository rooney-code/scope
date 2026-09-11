"""pytest 전역 설정: 디스플레이 없는 환경(CI/샌드박스)에서도 PySide6 위젯 테스트가
가능하도록 QT_QPA_PLATFORM을 offscreen으로 강제한다. 이미 설정되어 있으면 존중한다."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
