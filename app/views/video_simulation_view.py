"""실장비 없이 녹화 영상(.avi)으로 시험 절차를 검증하기 위한 컨트롤.

"시험 진행" 탭 하단에 항상 노출된다(카메라 종류와 무관) - 영상을 선택하면 지금 실행 중인
카메라가 무엇이든(실카메라/Mock/기존 --playback) 멈추고 그 영상 기반 재생으로 바뀐다
(InspectionViewModel.load_simulation_video 참고). 재생/일시정지는 작업자가 실제 장비를
다루듯 직접 제어한다 - 일시정지해도 InspectionViewModel._on_frame은 계속 호출되므로
(PlaybackCameraService가 마지막 프레임을 계속 재전송) 시험 절차 자체는 멈추지 않는다
(사용자 요청, 2026-09-15).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from app.viewmodels.inspection_viewmodel import InspectionViewModel

_NO_VIDEO_TEXT = "(선택된 영상 없음)"


class VideoSimulationView(QWidget):
    def __init__(self, viewmodel: InspectionViewModel, parent=None) -> None:
        super().__init__(parent)
        self.vm = viewmodel

        select_btn = QPushButton("영상 선택 (.avi)")
        select_btn.clicked.connect(self._on_select_video)
        self._path_label = QLabel(_NO_VIDEO_TEXT)

        # 재생/일시정지를 토글 버튼 하나로 - 두 버튼을 나란히 두는 대신, 지금 상태의
        # 반대 동작을 라벨로 보여준다(사용자 요청, 2026-09-16).
        self._is_playing = False
        self._play_pause_btn = QPushButton("재생")
        self._play_pause_btn.setEnabled(False)
        self._play_pause_btn.clicked.connect(self._on_play_pause_toggle)

        self._frame_counter_label = QLabel("프레임: -/-")
        self._processing_time_label = QLabel("프레임 처리: 평균 - ms")

        # 레드닷을 못 찾는 상황을 재현/분석할 때만 켜는 진단 로그 - 매 프레임 콘솔에
        # 검출/미검출 사유를 남긴다(RedDotDetector의 [detect] 후보 제외 로그 +
        # InspectionViewModel의 [detect] 최종 판단 로그, 사용자 요청 2026-09-15). 기본은
        # 꺼둔 상태(DetectionSettings.debug_logging 기본값)를 그대로 반영한다.
        self._debug_log_checkbox = QCheckBox("검출 로그 출력")
        self._debug_log_checkbox.setChecked(self.vm.settings.detection.debug_logging)
        self._debug_log_checkbox.toggled.connect(self._on_debug_log_toggled)

        # 프로그레스바(탐색 슬라이더) - 재생/일시정지만으로는 특정 지점(예: 35MOA 근처)을
        # 찾아가기 번거롭다는 요청(2026-09-15)에 따라 추가. 드래그 중에는 프레임 갱신으로
        # 슬라이더 값을 되돌리면 안 되므로(사용자 조작과 충돌) _on_frame_ready에서
        # isSliderDown()을 확인해서 건너뛴다. 실제 탐색은 손을 뗀 순간(sliderReleased)에만
        # 수행 - 드래그 중 매 픽셀마다 seek()를 부르면 과도하게 잦은 디코딩이 발생한다.
        self._seek_slider = QSlider(Qt.Horizontal)
        self._seek_slider.setEnabled(False)
        self._seek_slider.setRange(0, 0)
        self._seek_slider.sliderReleased.connect(self._on_seek_released)

        select_row = QHBoxLayout()
        select_row.addWidget(select_btn)
        select_row.addWidget(self._path_label, stretch=1)

        control_row = QHBoxLayout()
        control_row.addWidget(self._play_pause_btn)
        control_row.addWidget(self._frame_counter_label)
        control_row.addWidget(self._processing_time_label, stretch=1)
        control_row.addWidget(self._debug_log_checkbox)

        layout = QVBoxLayout(self)
        layout.addLayout(select_row)
        layout.addWidget(self._seek_slider)
        layout.addLayout(control_row)

        self.vm.frame_ready.connect(self._on_frame_ready)

    def _on_select_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "시험 영상 선택", "", "AVI Files (*.avi)")
        if not path:
            return
        self.vm.load_simulation_video(path)
        self._path_label.setText(Path(path).name)
        # load_simulation_video()는 항상 일시정지 상태로 시작한다(InspectionViewModel 참고) -
        # 토글 버튼 라벨/상태를 그에 맞춘다.
        self._is_playing = False
        self._play_pause_btn.setText("재생")
        self._play_pause_btn.setEnabled(True)
        self._seek_slider.setEnabled(True)

    def _on_play_pause_toggle(self) -> None:
        if self._is_playing:
            self.vm.pause_simulation_video()
        else:
            self.vm.play_simulation_video()
        self._is_playing = not self._is_playing
        self._play_pause_btn.setText("일시정지" if self._is_playing else "재생")

    def _on_seek_released(self) -> None:
        self.vm.seek_simulation_video(self._seek_slider.value())

    def _on_debug_log_toggled(self, checked: bool) -> None:
        self.vm.settings.detection.debug_logging = checked

    def _on_frame_ready(self, _frame) -> None:
        # 카메라가 영상 재생(PlaybackCameraService의 video 모드)이 아니면 이 속성들이
        # 없으므로 덕 타이핑으로 조회한다 - LiveFeedView가 reset_tracker_each_frame을
        # 조회하는 것과 동일한 스타일(app/viewmodels/inspection_viewmodel.py 참고).
        current = getattr(self.vm.camera, "current_frame_index", None)
        total = getattr(self.vm.camera, "total_frame_count", None)
        if current is not None and total is not None:
            self._frame_counter_label.setText(f"프레임: {current}/{total}")
            # 사용자가 슬라이더를 드래그하는 중에는 값을 되돌리면 안 된다(조작과 충돌).
            if not self._seek_slider.isSliderDown():
                if self._seek_slider.maximum() != max(0, total - 1):
                    self._seek_slider.setRange(0, max(0, total - 1))
                self._seek_slider.setValue(current)
        else:
            self._frame_counter_label.setText("프레임: -/-")

        avg_ms = self.vm.avg_frame_processing_ms
        if avg_ms is not None:
            skip_n = self.vm.current_frame_skip_n
            # 처리 시간이 느려져 프레임을 건너뛰기 시작하면(스킵 없으면 skip_n=1) 그 사실을
            # 같이 보여준다 - 안 보이면 왜 반응이 느려졌는지 알 수 없다는 우려(사용자 요청,
            # 2026-09-16).
            skip_text = "매 프레임 처리" if skip_n <= 1 else f"{skip_n}프레임당 1회 처리"
            self._processing_time_label.setText(f"프레임 처리: 평균 {avg_ms:.1f} ms ({skip_text})")
