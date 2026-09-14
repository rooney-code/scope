"""연결된 IDS 카메라의 NodeMap에 있는 모든 GenICam 노드 이름을 나열한다.

IDS peak Cockpit의 "기능 트리"(전문가) 화면이 라이선스로 잠겨 있어 원본 노드 이름을 볼 수
없을 때, 이 스크립트로 직접 확인한다 - 특히 BalanceRatioSelector/BalanceRatio처럼
"There is no node with the given name" 에러가 나는 노드의 실제 이름을 찾는 용도
(core/camera/ids_peak_camera_service.py 참고, 2026-09-14).

실행 (가상환경 활성화 상태, 카메라가 연결/인식된 상태에서):
    python scripts/list_camera_nodes.py            # 전체 노드 이름 나열
    python scripts/list_camera_nodes.py Balance     # 이름에 "Balance"가 들어간 노드만
"""
from __future__ import annotations

import sys


def main() -> int:
    keyword = sys.argv[1].lower() if len(sys.argv) > 1 else None

    from ids_peak import ids_peak as peak  # type: ignore

    peak.Library.Initialize()
    try:
        device_manager = peak.DeviceManager.Instance()
        device_manager.Update()
        devices = device_manager.Devices()
        if not devices:
            print("연결된 IDS 카메라를 찾을 수 없습니다.")
            return 1

        target = devices[0]
        print(f"카메라: {target.SerialNumber()} 사용 중...\n")
        device = target.OpenDevice(peak.DeviceAccessType_Control)
        nodemap = device.RemoteDevice().NodeMaps()[0]

        rows: list[tuple[str, str, str]] = []
        for node in nodemap.Nodes():
            name = node.Name()
            if keyword and keyword not in name.lower():
                continue
            try:
                display_name = node.DisplayName()
            except Exception:  # noqa: BLE001
                display_name = ""
            type_name = type(node).__name__
            rows.append((name, display_name, type_name))

        rows.sort(key=lambda r: r[0].lower())
        for name, display_name, type_name in rows:
            print(f"{name:40s} | {type_name:20s} | 표시명: {display_name}")
        print(f"\n총 {len(rows)}개 노드" + (f" (필터: '{sys.argv[1]}')" if keyword else ""))
        return 0
    finally:
        peak.Library.Close()


if __name__ == "__main__":
    sys.exit(main())
