"""지정한 GenICam 노드들의 현재 접근 상태(AccessStatus)와 값을 보여준다.

"Node is not writable" 에러가 나는 노드가 실제로 ReadOnly인지, 아니면 다른 이유인지
확실히 확인하기 위한 진단 스크립트(core/camera/ids_peak_camera_service.py 참고,
2026-09-14). list_camera_nodes.py는 노드 "이름"만 나열하고 접근 상태/값은 안 보여주므로
용도가 다르다 - 특정 노드를 콕 집어 상태를 봐야 할 때 이걸 쓴다.

실행 (가상환경 활성화 상태, 카메라가 연결/인식된 상태에서):
    python scripts/inspect_camera_nodes.py ExposureAuto GainAuto BalanceWhiteAuto \
        IsExposureAutoDisable IsExposureAutoLocked IsGainAutoDisable BrightnessAutoStatus
    (인자 없이 실행하면 아래 기본 목록을 사용)
"""
from __future__ import annotations

import sys

_DEFAULT_NODES = [
    "ExposureAuto",
    "GainAuto",
    "BalanceWhiteAuto",
    "BlackLevelAuto",
    "IsExposureAutoDisable",
    "IsExposureAutoLocked",
    "IsExposureAutoLocked_withSequencer",
    "IsGainAutoDisable",
    "BrightnessAutoStatus",
    "BrightnessAutoControl",
    "TLParamsLocked",
]

_ACCESS_STATUS_NAMES: dict[int, str] = {}


def main() -> int:
    node_names = sys.argv[1:] or _DEFAULT_NODES

    from ids_peak import ids_peak as peak  # type: ignore

    for attr in ("NotImplemented", "NotAvailable", "WriteOnly", "ReadOnly", "ReadWrite"):
        value = getattr(peak, f"NodeAccessStatus_{attr}")
        _ACCESS_STATUS_NAMES[value] = attr

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

        for name in node_names:
            if not nodemap.HasNode(name):
                print(f"{name:35s} | (이 노드 자체가 없음)")
                continue
            node = nodemap.FindNode(name)
            status = node.AccessStatus()
            status_name = _ACCESS_STATUS_NAMES.get(status, str(status))

            value_str = ""
            try:
                if hasattr(node, "CurrentEntry"):
                    value_str = node.CurrentEntry().SymbolicValue()
                elif hasattr(node, "Value"):
                    value_str = str(node.Value())
            except Exception as exc:  # noqa: BLE001
                value_str = f"(읽기 실패: {exc})"

            print(f"{name:35s} | 접근상태: {status_name:16s} | 값: {value_str}")

        return 0
    finally:
        peak.Library.Close()


if __name__ == "__main__":
    sys.exit(main())
