"""카메라 프리뷰 + 오버레이(원점, 레드닷 중심, 선택적 좌표축/안내선) 위젯.

카메라 해상도는 4K 이상이지만 화면(FHD)에 그대로 축소해서 보여주면 레드닷/눈금 같은 작은
디테일이 뭉개진다. 또한 원점(크로스헤어 교차점)은 카메라 설치 상태에 따라 이미지 중앙이
아닌 임의의 위치에 올 수 있다. 그래서:
  - 확대/자르기는 프레임의 기하학적 중앙이 아니라 **캘리브레이션된 원점**을 기준으로 한다.
  - 자르는 범위는 화면 비율(%)이 아니라 **MOA 단위**로 지정한다(트래블 검사가 35MOA까지
    다루므로 여유를 둔 기본값 45MOA 반경을 보여줌). 확대 슬라이더는 이 기본 범위를 더
    좁히는 배율로 동작한다.
  - 자른 영역은 **자기 자신의 가로세로 비율을 유지**한 채로만 리사이즈한다(원본 프레임의
    가로세로 비율로 강제로 맞추면 좌우가 늘어나 보이는 왜곡이 생김 - 실측으로 확인된 버그).
  - 원점/레드닷 마커, 좌표축/안내선 오버레이는 모두 **자르고 리사이즈까지 끝난 최종 화면
    좌표계**에서 그린다. 원본 해상도에서 그린 뒤 리사이즈하면 1px 두께 선이 보간 과정에서
    위치에 따라 보였다 안 보였다 하는 문제가 있었음(실측으로 확인된 버그).
  - 최종 QLabel 표시 시 `Qt.SmoothTransformation`을 사용한다 - 기본(FastTransformation,
    최근접이웃)은 축소 비율에 따라 얇은 선을 통째로 건너뛰어 사라지게 하는 경우가 있었음
    (실측으로 확인된 버그, 선 두께도 여유있게 2px로 상향).
  - 격자형(체크무늬) 오버레이 대신, 원점을 지나는 **좌표축 2개(MOA 눈금 포함)** + 레드닷
    위치를 지나는 **안내선 2개**만 그린다 - 안내선이 좌표축과 만나는 지점을 보면 레드닷의
    좌표를 바로 읽을 수 있다(사용자 피드백으로 촘촘한 격자에서 변경).
캘리브레이션이 아직 없거나(원점 모름) 스케일이 미확정(원점만 찾고 눈금 간격 미세조정 전,
대략적인 시작 추정치 상태)이면 "시험 진행" 탭에서는 원본 프레임을 그대로 보여준다 -
미확정 스케일로 크롭/오차 계산을 하면 극단적으로 확대되거나 터무니없는 숫자가 나오는
문제가 있었다(실측으로 확인, 2026-09-14). 이 상태는 PixelAngleCalibration.is_ready로
판단한다. 단, "캘리브레이션" 탭(calibration_mode)에서는 미확정 상태에서도 현재 추정치로
원점 마커/좌표축을 그려서 미세조정 워크플로에 시각적 피드백을 준다.
"""
from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.calibration.grid_overlay import draw_coordinate_axes, draw_dot_guide_lines
from core.calibration.pixel_angle_calibration import CalibrationProfile, PixelAngleCalibration
from core.vision.red_dot_detector import DetectionResult

_ORIGIN_MARKER_COLOR_BGR = (255, 200, 0)  # 하늘색 계열 - 레드닷(빨강)과 구분되게
_RED_DOT_MARKER_COLOR_BGR = (0, 0, 255)  # 레드닷 중심 표시: 빨간 점 하나
# 정상 범위(roi_margin_moa) 밖에서 폴백 검색으로 찾은 레드닷 - 조립 상태에 따라 실제로
# 발생할 수 있는 정상 상황이지만(사용자 요청, 2026-09-15), 정상 추적 중인 빨간 점과는
# 뚜렷이 구분되는 색(주황)으로 표시해 "지금은 수동 조정이 필요한 상태"임을 알린다.
_OUT_OF_RANGE_MARKER_COLOR_BGR = (0, 165, 255)


class _ClickableImageLabel(QLabel):
    """라벨 위 클릭 위치(라벨 로컬 좌표)를 그대로 알려주는 QLabel - 원본 프레임 좌표로의
    역변환은 LiveFeedView가 담당한다(크롭/리사이즈 단계를 알고 있는 쪽이 LiveFeedView이므로)."""

    clicked = Signal(float, float)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        self.clicked.emit(event.position().x(), event.position().y())


class LiveFeedView(QWidget):
    # 원본 프레임 좌표계 기준 클릭 위치 (x, y) - 캘리브레이션 모드에서만 발생(set_calibration_mode)
    frame_clicked_px = Signal(float, float)

    def __init__(self, default_view_range_moa: float = 45.0, display_target_size: int = 1600, parent=None) -> None:
        super().__init__(parent)
        self._calibration: PixelAngleCalibration | None = None
        self._default_view_range_moa = default_view_range_moa
        self._display_target_size = display_target_size
        self._calibration_mode = False  # True: 크롭/오버레이 없이 원본 전체 + 클릭 스냅 가능
        self._last_display_size: tuple[int, int] | None = None
        self._last_transform: tuple[float, float, float] | None = None

        self._image_label = _ClickableImageLabel("카메라 대기 중...")
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setMinimumSize(200, 200)
        # 라벨(전시 영역)과 실제 pixmap(레터박싱될 수 있음) 경계를 눈으로 구분하기 위해
        # 배경을 검게 칠한다 - 라벨 크기 자체는 이미 맞게 잡히고 있는지, 아니면 pixmap이
        # 작게 들어가 레터박싱되고 있는 것뿐인지 구분할 목적(사용자 요청, 2026-09-14).
        self._image_label.setStyleSheet("background-color: black; color: white;")
        self._image_label.clicked.connect(self._on_image_clicked)

        # 원점 기준 크롭 결과는 거의 정사각이 되도록 설계되어 있어(원점 있음 + 시험 진행
        # 모드일 때만) 표시 영역도 정사각으로 강제한다 - 그 외(원점 없음/캘리브레이션 모드)는
        # 원본 프레임 비율 그대로 컨테이너를 채운다(_relayout_square_image 참고). QLabel의
        # Qt.KeepAspectRatio는 "그 안의 pixmap"만 비율을 지키는 것이라, 라벨을 감싸는
        # 컨테이너에 stretch=1을 줘 남는 공간을 전부 차지하게 하고, resizeEvent에서 그
        # 컨테이너 크기를 기준으로 라벨 크기를 정한다.
        self._image_container = QWidget()
        image_container_layout = QHBoxLayout(self._image_container)
        image_container_layout.setContentsMargins(0, 0, 0, 0)
        image_container_layout.addStretch(1)
        image_container_layout.addWidget(self._image_label)
        image_container_layout.addStretch(1)
        self._image_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setRange(1, 5)
        self._zoom_slider.setValue(1)
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)
        self._zoom_label = QLabel(f"확대: 1단계 (원점 기준 ±{default_view_range_moa:.0f}MOA)")

        # 오버레이 요소별 개별 표시/숨기기 - 작업자마다 화면에 정보가 너무 많다고 느낄 수
        # 있어(사용자 피드백, 2026-09-13) 항목별로 끄고 켤 수 있게 한다. 전부 기본값은 표시.
        self._origin_axes_checkbox = QCheckBox("원점/기준선")
        self._origin_axes_checkbox.setChecked(True)
        self._origin_axes_checkbox.stateChanged.connect(self._on_overlay_toggle)

        self._red_dot_checkbox = QCheckBox("레드닷")
        self._red_dot_checkbox.setChecked(True)
        self._red_dot_checkbox.stateChanged.connect(self._on_overlay_toggle)

        self._guide_line_checkbox = QCheckBox("안내선(노란선)")
        self._guide_line_checkbox.setChecked(True)
        self._guide_line_checkbox.stateChanged.connect(self._on_overlay_toggle)

        self._info_text_checkbox = QCheckBox("오차 정보(R/U)")
        self._info_text_checkbox.setChecked(True)
        self._info_text_checkbox.stateChanged.connect(self._on_overlay_toggle)

        # RX/PROC FPS 표시도 다른 오버레이처럼 켜고 끌 수 있어야 한다는 요청(2026-09-16)에
        # 따라 체크박스를 추가한다.
        self._fps_checkbox = QCheckBox("FPS")
        self._fps_checkbox.setChecked(True)
        self._fps_checkbox.stateChanged.connect(self._on_overlay_toggle)

        self._overlay_checkboxes = [
            self._origin_axes_checkbox,
            self._red_dot_checkbox,
            self._guide_line_checkbox,
            self._info_text_checkbox,
            self._fps_checkbox,
        ]

        show_all_btn = QPushButton("전체 보이기")
        show_all_btn.clicked.connect(lambda: self._set_all_overlay_checkboxes(True))
        hide_all_btn = QPushButton("전체 숨기기")
        hide_all_btn.clicked.connect(lambda: self._set_all_overlay_checkboxes(False))

        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("확대 단계 (1~5, 원점 기준):"))
        zoom_row.addWidget(self._zoom_slider)
        zoom_row.addWidget(self._zoom_label)

        # 체크박스 5개 + 라벨 + 버튼 2개가 한 줄에 다 안 들어가서 두 줄로 나눈다(사용자
        # 요청, 2026-09-16) - 1줄: 원점/기준선・안내선・레드닷, 2줄: 오차정보・FPS(+버튼).
        overlay_row1 = QHBoxLayout()
        overlay_row1.addWidget(QLabel("오버레이 표시:"))
        overlay_row1.addWidget(self._origin_axes_checkbox)
        overlay_row1.addWidget(self._guide_line_checkbox)
        overlay_row1.addWidget(self._red_dot_checkbox)
        overlay_row1.addStretch(1)

        overlay_row2 = QHBoxLayout()
        overlay_row2.addWidget(self._info_text_checkbox)
        overlay_row2.addWidget(self._fps_checkbox)
        overlay_row2.addWidget(show_all_btn)
        overlay_row2.addWidget(hide_all_btn)
        overlay_row2.addStretch(1)

        # 확대/오버레이 컨트롤은 이 위젯 자신의 레이아웃에 넣지 않고 별도 위젯(controls_widget)
        # 으로 빼서 밖에서(MainWindow) 원하는 위치에 배치할 수 있게 한다 - 원래 영상 아래에
        # 있었는데, 그만큼 영상 세로 공간을 깎아먹는다는 피드백이 있었다(2026-09-14).
        # "레드닷 오차" 라벨은 여기 있었지만 시험 진행 탭의 Stage1AlignmentView가 보여주는
        # "오차"와 같은 값(calibration.to_moa 결과)의 표시 형식만 다른 중복이라 제거했다
        # (영상 위에 그려지는 R/U 오버레이 텍스트는 별개 기능이라 유지).
        self.controls_widget = QWidget()
        controls_layout = QVBoxLayout(self.controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addLayout(zoom_row)
        controls_layout.addLayout(overlay_row1)
        controls_layout.addLayout(overlay_row2)

        layout = QVBoxLayout(self)
        layout.addWidget(self._image_container, stretch=1)

        self._last_frame: np.ndarray | None = None
        self._last_detection: DetectionResult | None = None
        # 수신/처리 FPS - 처리 시간이 느린 환경에서 일부 프레임을 건너뛰기 시작하면(적응형
        # 프레임 스킵, InspectionViewModel._current_frame_skip_n 참고) 실제로 얼마나
        # 건너뛰고 있는지 라이브 화면에서 바로 보여달라는 요청(2026-09-16)에 따라 추가.
        self._received_fps = 0.0
        self._processed_fps = 0.0
        self._zoom_level = 1  # 1~5, 커질수록 view_range_moa가 좁아짐(더 확대)
        # True인 동안은 캘리브레이션 탭이어도 크롭하지 않고 원본 전체를 보여준다 - "그리드
        # 수동 검출"로 원점을 새로 클릭해야 할 때, 이미 크롭된(어쩌면 완전히 엉뚱한 위치를
        # 기준으로 한) 좁은 화면 안에서는 실제 크로스헤어가 아예 안 보일 수 있기 때문
        # (CalibrationView.origin_picking_changed 시그널로 MainWindow가 연결, 2026-09-14).
        self._calibration_origin_picking = False
        self._relayout_square_image()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._relayout_square_image()
        if self._last_frame is not None:
            self._render(self._last_frame, self._last_detection)

    def _is_cropped_view(self) -> bool:
        """지금 원점 기준으로 크롭/확대해서 보여줘야 하는 상태인지 - 시험 진행 탭과
        캘리브레이션 탭이 조건만 다를 뿐 같은 크롭 로직(_crop_and_resize_around_origin)을
        쓴다(사용자 요청: 캘리브레이션에서도 시험 진행과 동일한 방식으로 확대해서 원점/눈금을
        미세조정하기 쉽게 해달라, 2026-09-14).

        - 캘리브레이션 탭: 원점 지정 대기 상태(그리드 수동 검출 armed)가 아니고, 원점이
          하나라도 있으면(스케일 미확정이어도) 크롭한다 - 스케일이 아직 대략치여도 원점
          주변을 확대해서 보여줘야 미세조정이 쉬워지기 때문.
        - 시험 진행 탭: is_ready(원점+스케일 모두 확정)일 때만 크롭한다 - 미확정 스케일로
          크롭하면 범위 계산이 엉터리라서(_render의 profile 변수 참고).
        """
        if self._calibration_mode:
            return (
                not self._calibration_origin_picking
                and self._calibration is not None
                and self._calibration.profile is not None
            )
        return self._calibration is not None and self._calibration.is_ready

    def set_calibration_origin_picking(self, enabled: bool) -> None:
        self._calibration_origin_picking = enabled
        if self._last_frame is not None:
            self._render(self._last_frame, self._last_detection)

    def _relayout_square_image(self) -> None:
        """실제로 원점 기준 크롭을 보여줄 때만(_is_cropped_view) 라벨을 정사각으로 고정한다 -
        view_range_moa가 x/y 동일해 크롭 결과가 거의 정사각이 되도록 설계됐기 때문이다.
        크롭하지 않을 때(원점 없음, 원점 지정 대기 중, 시험 진행 탭에서 스케일 미확정)는
        원본 프레임 자체가 정사각이 아니므로(예: 3088x2076, 가로가 긴 비율) 억지로 정사각에
        맞추면 위아래 레터박스 여백만 커진다 - 이때는 컨테이너를 그대로 채운다(사용자 피드백:
        원점 못 찾았을 때 화면을 꽉 채워 보여줘야 한다, 2026-09-14)."""
        if self._is_cropped_view():
            side = max(200, min(self._image_container.width(), self._image_container.height(), 960))
            self._image_label.setFixedSize(side, side)
        else:
            w = max(200, min(self._image_container.width(), 960))
            h = max(200, min(self._image_container.height(), 960))
            self._image_label.setFixedSize(w, h)

    def set_calibration(self, calibration: PixelAngleCalibration) -> None:
        self._calibration = calibration

    def set_calibration_mode(self, enabled: bool) -> None:
        """캘리브레이션 탭이 활성화됐을 때 켠다 - 크롭/격자오버레이 없이 원본 프레임 전체를
        보여주고(먼 tick도 클릭 가능해야 하므로), 클릭 시 frame_clicked_px로 원본 좌표를 알려준다."""
        self._calibration_mode = enabled
        if self._last_frame is not None:
            self._render(self._last_frame, self._last_detection)

    def _on_image_clicked(self, label_x: float, label_y: float) -> None:
        if not self._calibration_mode or self._last_display_size is None:
            return
        disp_w, disp_h = self._last_display_size
        label_w, label_h = self._image_label.width(), self._image_label.height()
        if disp_w <= 0 or disp_h <= 0 or label_w <= 0 or label_h <= 0:
            return

        # QLabel.setPixmap(..., Qt.KeepAspectRatio)로 인한 letterbox 보정 (ClickableFrameLabel과 동일 원리)
        label_scale = min(label_w / disp_w, label_h / disp_h)
        offset_x = (label_w - disp_w * label_scale) / 2
        offset_y = (label_h - disp_h * label_scale) / 2
        disp_x = (label_x - offset_x) / label_scale
        disp_y = (label_y - offset_y) / label_scale
        if not (0 <= disp_x <= disp_w and 0 <= disp_y <= disp_h):
            return  # letterbox 여백 클릭은 무시

        if self._last_transform is None:
            orig_x, orig_y = disp_x, disp_y
        else:
            x0, y0, scale = self._last_transform
            orig_x, orig_y = disp_x / scale + x0, disp_y / scale + y0
        self.frame_clicked_px.emit(orig_x, orig_y)

    def _on_overlay_toggle(self, _state: int) -> None:
        if self._last_frame is not None:
            self._render(self._last_frame, self._last_detection)

    def _set_all_overlay_checkboxes(self, checked: bool) -> None:
        for checkbox in self._overlay_checkboxes:
            checkbox.setChecked(checked)  # 각 setChecked가 _on_overlay_toggle을 호출해 다시 그림

    def _on_zoom_changed(self, level: int) -> None:
        self._zoom_level = level
        view_range = self._current_view_range_moa()
        self._zoom_label.setText(f"확대: {level}단계 (원점 기준 ±{view_range:.0f}MOA)")
        if self._last_frame is not None:
            self._render(self._last_frame, self._last_detection)

    def _current_view_range_moa(self) -> float:
        return self._default_view_range_moa / self._zoom_level

    def _current_axis_label_step_moa(self) -> float:
        """숫자 라벨은 대략 8~12개 정도만 보이도록 간격을 정한다(눈금 자체는 1MOA 고정).

        스케일(px/MOA)이 카메라마다 다르므로 숫자 라벨까지 view_range와 무관하게
        고정하면 배율에 따라 너무 빽빽하거나 너무 뜸해질 수 있다 - view_range_moa에 맞춰
        1/2/5/10/20/50 중 가장 적절한 라벨 간격을 고른다(1MOA 눈금 간격의 배수가 되도록).
        """
        target_labels = 10
        raw_step = (self._current_view_range_moa() * 2) / target_labels
        nice_steps = [1, 2, 5, 10, 20, 50, 100]
        return min(nice_steps, key=lambda s: abs(s - raw_step))

    def on_frame(self, frame_bgr: np.ndarray) -> None:
        self._last_frame = frame_bgr
        self._render(frame_bgr, self._last_detection)

    def on_detection(self, result: DetectionResult) -> None:
        self._last_detection = result
        if self._last_frame is not None:
            self._render(self._last_frame, result)

    def on_fps_stats(self, received_fps: float, processed_fps: float) -> None:
        # 값만 저장하고 여기서 다시 그리지 않는다 - InspectionViewModel이 이 시그널을
        # frame_ready와 거의 같은 빈도(프레임마다, 건너뛴 프레임 포함)로 내보내는데, 매번
        # 강제로 재렌더(크롭/리사이즈/그리기/QImage 변환까지 포함하는 무거운 작업)하면
        # 프레임당 렌더 횟수가 최대 3배(on_frame + on_detection + 이 시그널 x2)까지 늘어나
        # 프로그램이 눈에 띄게 느려지는 회귀가 있었다(사용자 지적, 2026-09-16). 어차피 다음
        # on_frame()/on_detection() 호출이 곧바로(같은 프레임 처리 안에서) 뒤따라와 최신
        # 숫자로 다시 그려주므로, 한두 프레임 정도 지연되는 건 체감되지 않는다.
        self._received_fps = received_fps
        self._processed_fps = processed_fps

    def _render(self, frame_bgr: np.ndarray, detection: DetectionResult | None) -> None:
        # 원점(캘리브레이션) 유무/모드에 따라 정사각 강제 여부가 달라지므로(_relayout_square_image
        # 참고) 매 프레임 다시 반영한다 - 시험 도중 캘리브레이션이 막 완료된 경우 등, 창 크기
        # 변경 없이도 라벨 형태가 바뀌어야 하는 시점을 놓치지 않기 위함.
        self._relayout_square_image()

        # 원점만 있고 스케일(px_per_moa)이 아직 대략적인 시작 추정치인 미확정 캘리브레이션은
        # "시험 진행" 탭의 크롭/오차 계산에 쓰면 극단적으로 확대되거나 터무니없는 숫자가
        # 나온다(실측으로 확인된 문제, 2026-09-14) - is_ready(원점+x/y 스케일 모두 확정)일
        # 때만 profile을 실제로 넘기고, 그 전에는 캘리브레이션이 아예 없는 것처럼(원본 그대로)
        # 취급한다. 캘리브레이션 탭은 미확정이어도 크롭해야 하므로 raw_profile을 따로 쓴다
        # (_is_cropped_view 참고 - 사용자 요청: 캘리브레이션도 시험 진행과 동일하게 원점
        # 기준으로 확대해서 미세조정하기 쉽게 해달라, 2026-09-14).
        profile = self._calibration.profile if self._calibration is not None and self._calibration.is_ready else None
        raw_profile = self._calibration.profile if self._calibration is not None else None
        crop_profile = raw_profile if self._calibration_mode else profile

        # 정상 범위 밖에서 찾은 레드닷(out_of_range)은 원점 기준 크롭 화면 밖에 있을 수
        # 있다 - 크롭한 채로는 사용자가 조정에 필요한 위치를 아예 볼 수 없으므로, 이 상태인
        # 동안은 원본 전체를 보여준다(사용자 요청, 2026-09-15).
        out_of_range = detection is not None and detection.found and detection.out_of_range

        if out_of_range and not self._calibration_mode:
            display, transform = self._scale_to_display_size(frame_bgr)
        elif self._is_cropped_view():
            display, transform = self._crop_and_resize_around_origin(frame_bgr, crop_profile)
        elif self._calibration_mode:
            # 원점이 아직 없거나(자동/수동 검출 전) 원점 지정 대기 상태(그리드 수동 검출
            # armed)면 크롭하지 않고 원본 전체를 보여준다 - 실제 크로스헤어가 화면 어디에
            # 있는지 모르는 상태에서 크롭하면 그 크로스헤어 자체가 화면 밖으로 나가 클릭할
            # 수 없게 될 수 있기 때문.
            display, transform = self._scale_to_display_size(frame_bgr)
        else:
            display, transform = self._crop_and_resize_around_origin(frame_bgr, profile)

        self._last_display_size = (display.shape[1], display.shape[0])
        self._last_transform = transform

        if self._calibration_mode:
            # 레드닷/안내선/오차 텍스트는 캘리브레이션 대상이 아니므로 그리지 않는다. 원점
            # 마커/좌표축은 스케일이 미확정이어도 현재 추정치로 그린다 - 그래야 자동/수동
            # 검출 직후 원점 위치를 바로 확인하고, 눈금 간격 미세조정 버튼을 누르며 빨간
            # 좌표축이 실제 그리드와 맞는지 눈으로 보고 맞춰나갈 수 있다(사용자 요청,
            # 2026-09-14 - 이 시각적 피드백이 없으면 미세조정 워크플로 자체가 불가능함).
            if raw_profile is not None:
                ox, oy = self._transform_point((raw_profile.origin_px_x, raw_profile.origin_px_y), transform)
                cv2.drawMarker(display, (int(round(ox)), int(round(oy))), _ORIGIN_MARKER_COLOR_BGR, cv2.MARKER_CROSS, 20, 2)
                display_profile = self._transform_profile(raw_profile, transform)
                display = draw_coordinate_axes(
                    display,
                    display_profile,
                    tick_step_moa=1.0,
                    label_step_moa=self._current_axis_label_step_moa(),
                    max_moa_range=self._current_view_range_moa(),
                )

            self._draw_fps_text(display)

            rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
            self._image_label.setPixmap(
                QPixmap.fromImage(qimg).scaled(
                    self._image_label.width(), self._image_label.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
            return

        display_profile = self._transform_profile(profile, transform)

        dot_px_display: tuple[float, float] | None = None
        if detection is not None and detection.found:
            dot_px_display = self._transform_point(detection.center_px, transform)

        show_origin_axes = self._origin_axes_checkbox.isChecked()
        show_red_dot = self._red_dot_checkbox.isChecked()
        show_guide_lines = self._guide_line_checkbox.isChecked()
        show_info_text = self._info_text_checkbox.isChecked()

        if show_origin_axes and display_profile is not None:
            display = draw_coordinate_axes(
                display,
                display_profile,
                tick_step_moa=1.0,
                label_step_moa=self._current_axis_label_step_moa(),
                max_moa_range=self._current_view_range_moa(),
            )
            # 원점 마커도 "원점/기준선" 항목에 포함 - 좌표축과 같은 그룹으로 취급한다
            # (사용자 요청: 원점 및 기준선을 한 항목으로 켜고 끔).
            ox, oy = int(round(display_profile.origin_px_x)), int(round(display_profile.origin_px_y))
            cv2.drawMarker(display, (ox, oy), _ORIGIN_MARKER_COLOR_BGR, cv2.MARKER_CROSS, 14, 2)

        if show_guide_lines and dot_px_display is not None:
            display = draw_dot_guide_lines(display, dot_px_display)

        # 레드닷: 검출된 중심점을 점 하나로 표시 - 범위 밖 폴백 결과는 주황색으로 구분
        if show_red_dot and dot_px_display is not None:
            dot_color = _OUT_OF_RANGE_MARKER_COLOR_BGR if out_of_range else _RED_DOT_MARKER_COLOR_BGR
            cv2.circle(display, (int(round(dot_px_display[0])), int(round(dot_px_display[1]))), 6 if out_of_range else 4, dot_color, -1)

        if out_of_range:
            self._draw_out_of_range_banner(display)

        self._draw_offset_text(display, detection, profile, show_info_text)
        self._draw_fps_text(display)

        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        self._image_label.setPixmap(
            QPixmap.fromImage(qimg).scaled(
                self._image_label.width(),
                self._image_label.height(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,  # 기본(FastTransformation, 최근접이웃)은 1px 안내선을
                # 축소 과정에서 통째로 건너뛰어 사라지게 하는 경우가 있어 스무스 스케일링 사용
            )
        )

    def _draw_out_of_range_banner(self, display: np.ndarray) -> None:
        """레드닷이 정상 범위 밖에서 폴백 검색으로 발견됐을 때 화면 상단에 경고 배너를
        띄운다 - cv2.putText는 한글 글리프가 없어 깨지므로(_draw_offset_text와 동일한
        이유) 영문 문구만 사용한다."""
        text = "DOT OUT OF RANGE - ADJUST TO BRING IT BACK TO ORIGIN"
        w = display.shape[1]
        font_scale = max(0.7, w / 1600)
        thickness = max(2, int(font_scale * 2))
        (text_w, text_h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        pad = 10
        x = max(pad, (w - text_w) // 2)
        cv2.rectangle(display, (x - pad, 0), (x + text_w + pad, text_h + 2 * pad), _OUT_OF_RANGE_MARKER_COLOR_BGR, -1)
        cv2.putText(
            display, text, (x, text_h + pad // 2 + 4), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), thickness
        )

    def _draw_offset_text(
        self, display: np.ndarray, detection: DetectionResult | None, profile, show_on_image: bool
    ) -> None:
        """레드닷이 원점 기준 좌우/상하로 몇 MOA 벗어나 있는지 영상 우측 상단에 표시.

        원래 좌측 하단에 있었는데, 작업자 시선이 화면 위쪽(레드닷/원점 부근)에 머무는데
        아래쪽을 봐야 해서 불편하다는 지적(2026-09-16)에 따라 우측 상단으로 옮겼다 - 좌측
        상단은 RX/PROC FPS(_draw_fps_text)가 쓰므로 겹치지 않게 반대쪽에 둔다.

        cv2.putText는 한글 글리프가 없어 깨지므로 영문 라벨(R/L/U/D)만 사용한다. 같은 값을
        보여주는 Qt 라벨은 시험 진행 탭의 Stage1AlignmentView.offset_label과 중복이라
        제거했다(2026-09-14) - show_on_image(오버레이 표시 > "오차 정보(R/U)" 체크박스)로
        영상 위 텍스트만 켜고 끈다.
        """
        if not show_on_image or detection is None or not detection.found or profile is None or self._calibration is None:
            return

        x_moa, y_moa = self._calibration.to_moa(detection.center_px)
        lr = "R" if x_moa >= 0 else "L"
        ud = "U" if y_moa >= 0 else "D"
        text = f"{lr} {abs(x_moa):.2f} MOA  /  {ud} {abs(y_moa):.2f} MOA"

        w = display.shape[1]
        font_scale = max(1.0, w / 1400)  # 해상도에 비례해 글자 크기 자동 조절 (가독성 확보)
        thickness = max(2, int(font_scale * 2))
        (text_w, text_h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        pad = 10
        x0 = w - text_w - pad - 6
        cv2.rectangle(
            display,
            (x0 - 6, pad - 6),
            (x0 + text_w + 6, pad + text_h + 10),
            (0, 0, 0),
            -1,
        )
        cv2.putText(
            display, text, (x0, pad + text_h), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 255, 255), thickness
        )

    def _draw_fps_text(self, display: np.ndarray) -> None:
        """수신 FPS(카메라에서 실제로 들어오는 속도)/처리 FPS(검출+상태기계를 실제로
        처리한 속도)를 영상 좌측 상단에 표시 - 처리 시간이 느린 환경에서 적응형 프레임
        스킵(InspectionViewModel._current_frame_skip_n)이 실제로 얼마나 건너뛰고 있는지
        라이브 화면에서 바로 보고 싶다는 요청(2026-09-16)에 따라 추가. cv2.putText는
        한글 글리프가 없어 영문 라벨만 사용한다(_draw_offset_text와 동일한 이유). 다른
        오버레이처럼 "FPS" 체크박스로 켜고 끌 수 있다 - 캘리브레이션 탭에서도 이 체크박스가
        그대로 적용된다(사용자 요청, 2026-09-16).
        """
        if not self._fps_checkbox.isChecked():
            return
        # 두 값을 위아래로 보고 싶다는 요청(2026-09-16)에 따라 한 줄이 아니라 두 줄로 그린다.
        lines = [f"RX FPS: {self._received_fps:.1f}", f"PROC FPS: {self._processed_fps:.1f}"]
        w = display.shape[1]
        font_scale = max(0.7, w / 1600)
        thickness = max(2, int(font_scale * 2))
        sizes = [cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)[0] for line in lines]
        text_w = max(size[0] for size in sizes)
        line_h = max(size[1] for size in sizes)
        line_gap = int(line_h * 0.6)
        pad = 10
        box_h = len(lines) * line_h + (len(lines) - 1) * line_gap + 10
        cv2.rectangle(display, (pad - 6, pad - 6), (pad + text_w + 6, pad + box_h), (0, 0, 0), -1)
        for i, line in enumerate(lines):
            y = pad + line_h + i * (line_h + line_gap)
            cv2.putText(display, line, (pad, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 255, 0), thickness)

    def _scale_to_display_size(self, frame: np.ndarray) -> tuple[np.ndarray, tuple[float, float, float]]:
        """캘리브레이션 모드용: 크롭 없이 원본 전체를 display_target_size에 맞게 비율 유지
        축소만 한다(원거리 tick도 화면에 보이고 클릭 가능해야 하므로)."""
        h, w = frame.shape[:2]
        scale = self._display_target_size / max(w, h)
        new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        return resized, (0.0, 0.0, scale)

    def _crop_and_resize_around_origin(
        self, frame: np.ndarray, profile: CalibrationProfile | None
    ) -> tuple[np.ndarray, tuple[float, float, float] | None]:
        """캘리브레이션된 원점을 중심으로 ±view_range_moa 영역만 원본 해상도로 잘라낸다.

        캘리브레이션이 아직 없으면(원점 위치를 모름) 원본 프레임을 그대로 반환한다 - 원점을
        모르는 상태에서 프레임 중앙을 임의로 원점처럼 취급하면 실제 설치 상태와 맞지 않는
        위치를 보여주게 되므로 절대 하지 않는다.

        반환값: (표시용 이미지, transform). transform은 (x0, y0, scale) - 원본 프레임 좌표
        (px, py)를 표시용 좌표로 변환하려면 ((px-x0)*scale, (py-y0)*scale). 크롭이 적용되지
        않았으면 transform은 None(=원본 좌표를 그대로 사용).
        """
        if profile is None:
            return frame, None

        h, w = frame.shape[:2]
        view_range_moa = self._current_view_range_moa()

        half_w_px = view_range_moa * profile.px_per_moa_x
        half_h_px = view_range_moa * profile.px_per_moa_y

        x0 = int(max(0, profile.origin_px_x - half_w_px))
        x1 = int(min(w, profile.origin_px_x + half_w_px))
        y0 = int(max(0, profile.origin_px_y - half_h_px))
        y1 = int(min(h, profile.origin_px_y + half_h_px))

        if x1 <= x0 or y1 <= y0:
            return frame, None

        cropped = frame[y0:y1, x0:x1]
        crop_h, crop_w = cropped.shape[:2]

        # 자기 자신의 가로세로 비율을 유지한 채로만 확대/축소 (원본 프레임 크기에 억지로
        # 맞추면 좌우 또는 상하가 늘어나 보이는 왜곡이 생김)
        scale = self._display_target_size / max(crop_w, crop_h)
        new_w, new_h = max(1, int(round(crop_w * scale))), max(1, int(round(crop_h * scale)))
        resized = cv2.resize(cropped, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        return resized, (float(x0), float(y0), scale)

    @staticmethod
    def _transform_point(point_px: tuple[float, float], transform: tuple[float, float, float] | None) -> tuple[float, float]:
        if transform is None:
            return point_px
        x0, y0, scale = transform
        return (point_px[0] - x0) * scale, (point_px[1] - y0) * scale

    @classmethod
    def _transform_profile(
        cls, profile: CalibrationProfile | None, transform: tuple[float, float, float] | None
    ) -> CalibrationProfile | None:
        """캘리브레이션 프로파일(원점+스케일)을 크롭/리사이즈 후의 표시 좌표계로 변환한
        새 프로파일을 만든다 - 격자/원점 마커를 최종 화면 좌표계에서 그리기 위함."""
        if profile is None:
            return None
        if transform is None:
            return profile

        ox, oy = cls._transform_point((profile.origin_px_x, profile.origin_px_y), transform)
        scale = transform[2]
        return CalibrationProfile(
            camera_id=profile.camera_id,
            origin_px_x=ox,
            origin_px_y=oy,
            px_per_moa_x=profile.px_per_moa_x * scale,
            px_per_moa_y=profile.px_per_moa_y * scale,
        )
