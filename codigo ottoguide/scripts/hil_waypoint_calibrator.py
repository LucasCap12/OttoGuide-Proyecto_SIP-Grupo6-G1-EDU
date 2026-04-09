#!/usr/bin/env python3
"""Calibrador HIL de waypoints usando AMCL y escritura atomica de JSON."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Optional

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node


VALID_NODE_IDS = {"I", "1", "2", "3", "F"}


def parse_args() -> argparse.Namespace:
    """Parsea argumentos CLI y valida node-id."""
    parser = argparse.ArgumentParser(
        description="Captura pose AMCL y actualiza pose_2d del waypoint en JSON.",
    )
    parser.add_argument(
        "--node-id",
        required=True,
        choices=sorted(VALID_NODE_IDS),
        help="Identificador de waypoint logico: I, 1, 2, 3, F.",
    )
    return parser.parse_args()


def quaternion_to_yaw(qx: float, qy: float, qz: float, qw: float) -> float:
    """Convierte cuaternion a yaw en radianes."""
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    return math.atan2(siny_cosp, cosy_cosp)


class AmclPoseCaptureNode(Node):
    """Nodo ROS 2 para capturar una sola pose valida de /amcl_pose."""

    def __init__(self) -> None:
        super().__init__("hil_waypoint_calibrator")
        self._captured_pose: Optional[tuple[float, float, float]] = None
        self._subscription = self.create_subscription(
            PoseWithCovarianceStamped,
            "/amcl_pose",
            self._on_pose,
            10,
        )

    @property
    def captured_pose(self) -> Optional[tuple[float, float, float]]:
        """Retorna pose capturada o None si aun no hay datos."""
        return self._captured_pose

    def _on_pose(self, msg: PoseWithCovarianceStamped) -> None:
        """Captura primer mensaje valido y libera suscripcion."""
        x = float(msg.pose.pose.position.x)
        y = float(msg.pose.pose.position.y)
        q = msg.pose.pose.orientation
        theta = float(quaternion_to_yaw(float(q.x), float(q.y), float(q.z), float(q.w)))
        self._captured_pose = (x, y, theta)
        if self._subscription is not None:
            self.destroy_subscription(self._subscription)
            self._subscription = None


def wait_for_amcl_pose() -> tuple[float, float, float]:
    """Inicializa ROS 2, espera pose y libera contexto de forma limpia."""
    rclpy.init(args=None)
    node = AmclPoseCaptureNode()
    try:
        while rclpy.ok() and node.captured_pose is None:
            rclpy.spin_once(node, timeout_sec=0.2)
        if node.captured_pose is None:
            raise RuntimeError("No fue posible capturar pose desde /amcl_pose.")
        return node.captured_pose
    finally:
        node.destroy_node()
        rclpy.shutdown()


def atomic_write_json(target_path: Path, payload: dict) -> None:
    """Escribe JSON de forma atomica mediante archivo temporal y replace."""
    parent = target_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(prefix="mvp_tour_script_", suffix=".tmp", dir=str(parent))
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def update_waypoint_pose(
    *,
    json_path: Path,
    node_id: str,
    x: float,
    y: float,
    theta: float,
) -> None:
    """Actualiza pose_2d de un waypoint y persiste en disco de forma atomica."""
    raw = json_path.read_text(encoding="utf-8")
    doc = json.loads(raw)
    waypoints = doc.get("waypoints", [])
    if not isinstance(waypoints, list):
        raise ValueError("Estructura invalida: 'waypoints' debe ser una lista.")

    target = None
    for waypoint in waypoints:
        if isinstance(waypoint, dict) and waypoint.get("waypoint_id") == node_id:
            target = waypoint
            break

    if target is None:
        raise ValueError(f"No se encontro waypoint_id='{node_id}' en el script.")

    pose_2d = target.get("pose_2d")
    if not isinstance(pose_2d, dict):
        pose_2d = {}
        target["pose_2d"] = pose_2d

    pose_2d["x"] = round(float(x), 3)
    pose_2d["y"] = round(float(y), 3)
    pose_2d["theta"] = round(float(theta), 3)

    atomic_write_json(json_path, doc)


def main() -> int:
    """Punto de entrada principal para calibracion de un nodo logico."""
    args = parse_args()
    node_id = args.node_id
    json_path = Path(__file__).resolve().parents[1] / "data" / "mvp_tour_script.json"

    x, y, theta = wait_for_amcl_pose()
    update_waypoint_pose(
        json_path=json_path,
        node_id=node_id,
        x=x,
        y=y,
        theta=theta,
    )

    print(
        json.dumps(
            {
                "node_id": node_id,
                "x": round(x, 3),
                "y": round(y, 3),
                "theta": round(theta, 3),
                "json_path": str(json_path),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
