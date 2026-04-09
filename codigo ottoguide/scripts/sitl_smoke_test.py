from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SDK_ROOT = PROJECT_ROOT / "libs" / "unitree_sdk2_python-master"

for path in (PROJECT_ROOT, SDK_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def _format_joint_sample(low_state: Any) -> dict[str, Any]:
    motor_state = getattr(low_state, "motor_state", [])
    joints = []
    for index in range(min(6, len(motor_state))):
        joint = motor_state[index]
        joints.append(
            {
                "index": index,
                "q": round(float(getattr(joint, "q", 0.0)), 4),
                "dq": round(float(getattr(joint, "dq", 0.0)), 4),
                "tau_est": round(float(getattr(joint, "tau_est", 0.0)), 4),
            }
        )

    imu_state = getattr(low_state, "imu_state", None)
    imu_rpy = list(getattr(imu_state, "rpy", [])) if imu_state is not None else []

    return {
        "joints": joints,
        "imu_rpy": [round(float(value), 4) for value in imu_rpy[:3]],
    }


async def _await_low_state(timeout_s: float) -> dict[str, Any]:
    low_state_event = asyncio.Event()
    captured: dict[str, Any] = {}

    try:
        from unitree_sdk2py.core.channel import ChannelSubscriber
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_
    except ImportError as exc:
        raise RuntimeError(
            f"No se pudo importar unitree_sdk2py: {exc}. "
            "Verifica que libs/unitree_sdk2_python-master este disponible en el workspace."
        ) from exc

    def handle_low_state(message: LowState_) -> None:
        if not captured:
            captured["sample"] = message
            low_state_event.set()

    subscriber = ChannelSubscriber("rt/lowstate", LowState_)
    subscriber.Init(handle_low_state, 10)

    await asyncio.wait_for(low_state_event.wait(), timeout=timeout_s)
    return captured["sample"]


async def run_smoke_test(args: argparse.Namespace) -> int:
    try:
        from hardware.interface import MotionCommand
        from hardware.sim_adapter import UnitreeG1SimAdapter
    except ImportError as exc:
        raise RuntimeError(
            f"No se pudo importar la capa hardware: {exc}. "
            "Ejecuta el script desde la raiz del proyecto."
        ) from exc

    adapter = UnitreeG1SimAdapter()
    await adapter.initialize()
    print("[smoke] adapter initialized:", json.dumps(await adapter.get_state(), ensure_ascii=True))

    await adapter.stand()
    print("[smoke] stand() dispatched")

    if args.send_zero_move:
        await adapter.move(MotionCommand(linear_x=0.0, angular_z=0.0, duration_ms=250))
        print("[smoke] zero-velocity move dispatched")

    low_state = await _await_low_state(args.low_state_timeout)
    print("[smoke] lowstate sample:")
    print(json.dumps(_format_joint_sample(low_state), ensure_ascii=True, indent=2))

    await adapter.damp()
    print("[smoke] damp() dispatched")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test DDS para unitree_mujoco en domain 1")
    parser.add_argument("--low-state-timeout", type=float, default=5.0)
    parser.add_argument("--send-zero-move", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(run_smoke_test(args))


if __name__ == "__main__":
    raise SystemExit(main())