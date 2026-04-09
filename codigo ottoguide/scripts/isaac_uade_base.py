from __future__ import annotations

import argparse
import sys
from pathlib import Path

"""
@TASK: Modelar entorno UADE paramétrico en Isaac Lab con primitivas de omni.isaac.core.objects.
@INPUT: Parámetros CLI de geometría, steps y opciones de app launcher Isaac Lab.
@OUTPUT: Escena 3D con zona entrada, planta baja y patio usando cuboides/cilindros con colisión activa.
@CONTEXT: Script SRE de simulación para alimentar sensores virtuales y SLAM/Nav2 en SITL.
@SECURITY: Manejo explícito de ImportError para evitar colapso en entornos sin Omniverse.
STEP [1]: Resolver imports y paths de Isaac Lab sin modificar FSM de src ni contenido de libs.
STEP [2]: Construir geometría paramétrica con DynamicCuboid y DynamicCylinder.
STEP [3]: Forzar colisionadores y bandera cinemática en prims USD para rebote correcto de lidar.
STEP [4]: Ejecutar stepping de simulación y cierre limpio de la aplicación.
"""


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIMLAB_ROOT = PROJECT_ROOT / "libs" / "unitree_sim_isaaclab-main"

for path in (PROJECT_ROOT, SIMLAB_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Base 3D scene para Isaac Lab")
    parser.add_argument("--steps", type=int, default=240)
    parser.add_argument("--entrada_wall_length", type=float, default=5.0)
    parser.add_argument("--entrada_wall_height", type=float, default=1.0)
    parser.add_argument("--entrada_corridor_width", type=float, default=2.5)
    parser.add_argument("--patio_length", type=float, default=10.0)
    parser.add_argument("--patio_width", type=float, default=8.0)
    parser.add_argument("--patio_wall_height", type=float, default=1.2)
    parser.add_argument("--wall_thickness", type=float, default=0.15)
    parser.add_argument("--planta_obstacle_radius", type=float, default=0.5)
    parser.add_argument("--planta_obstacle_height", type=float, default=1.0)
    return parser


def _import_stack():
    try:
        from isaaclab.app import AppLauncher
        from omni.isaac.core import World
        from omni.isaac.core.objects import DynamicCuboid, DynamicCylinder
        from pxr import UsdPhysics
        return AppLauncher, World, DynamicCuboid, DynamicCylinder, UsdPhysics
    except ImportError as exc:
        print(
            "@OUTPUT: Isaac Lab/Omniverse no disponible. "
            f"Se omite ejecución de escena. Detalle: {exc}"
        )
        return None


def _configure_collision_and_kinematic(primitive: object, usd_physics: object) -> None:
    prim = primitive.prim
    usd_physics.CollisionAPI.Apply(prim)
    rigid_api = usd_physics.RigidBodyAPI.Apply(prim)
    rigid_api.CreateKinematicEnabledAttr(True)
    if hasattr(primitive, "set_collision_enabled"):
        primitive.set_collision_enabled(True)


def main() -> int:
    stack = _import_stack()
    if stack is None:
        return 0

    AppLauncher, World, DynamicCuboid, DynamicCylinder, UsdPhysics = stack
    app_parser = build_parser()
    AppLauncher.add_app_launcher_args(app_parser)
    args_cli = app_parser.parse_args()

    app_launcher = AppLauncher(args_cli)
    simulation_app = app_launcher.app
    world = World(stage_units_in_meters=1.0, physics_dt=1.0 / 60.0, rendering_dt=1.0 / 60.0)
    world.scene.add_default_ground_plane()

    wall_z = args_cli.entrada_wall_height * 0.5
    corridor_y = args_cli.entrada_corridor_width * 0.5
    entrada_wall_left = DynamicCuboid(
        prim_path="/World/Entrada/MuroIzquierdo",
        name="entrada_muro_izquierdo",
        position=[-2.0, -corridor_y, wall_z],
        scale=[args_cli.entrada_wall_length, args_cli.wall_thickness, args_cli.entrada_wall_height],
        color=[0.20, 0.60, 0.95],
    )
    entrada_wall_right = DynamicCuboid(
        prim_path="/World/Entrada/MuroDerecho",
        name="entrada_muro_derecho",
        position=[-2.0, corridor_y, wall_z],
        scale=[args_cli.entrada_wall_length, args_cli.wall_thickness, args_cli.entrada_wall_height],
        color=[0.20, 0.60, 0.95],
    )

    planta_obstacle_a = DynamicCylinder(
        prim_path="/World/PlantaBaja/ObstaculoA",
        name="planta_obstaculo_a",
        position=[3.0, -1.2, args_cli.planta_obstacle_height * 0.5],
        radius=args_cli.planta_obstacle_radius,
        height=args_cli.planta_obstacle_height,
        color=[0.95, 0.62, 0.20],
    )
    planta_obstacle_b = DynamicCylinder(
        prim_path="/World/PlantaBaja/ObstaculoB",
        name="planta_obstaculo_b",
        position=[3.0, 1.2, args_cli.planta_obstacle_height * 0.5],
        radius=args_cli.planta_obstacle_radius,
        height=args_cli.planta_obstacle_height,
        color=[0.95, 0.62, 0.20],
    )

    patio_half_l = args_cli.patio_length * 0.5
    patio_half_w = args_cli.patio_width * 0.5
    patio_center_x = 9.0
    patio_wall_z = args_cli.patio_wall_height * 0.5
    patio_north = DynamicCuboid(
        prim_path="/World/Patio/MuroNorte",
        name="patio_muro_norte",
        position=[patio_center_x, patio_half_w, patio_wall_z],
        scale=[args_cli.patio_length, args_cli.wall_thickness, args_cli.patio_wall_height],
        color=[0.20, 0.78, 0.44],
    )
    patio_south = DynamicCuboid(
        prim_path="/World/Patio/MuroSur",
        name="patio_muro_sur",
        position=[patio_center_x, -patio_half_w, patio_wall_z],
        scale=[args_cli.patio_length, args_cli.wall_thickness, args_cli.patio_wall_height],
        color=[0.20, 0.78, 0.44],
    )
    patio_east = DynamicCuboid(
        prim_path="/World/Patio/MuroEste",
        name="patio_muro_este",
        position=[patio_center_x + patio_half_l, 0.0, patio_wall_z],
        scale=[args_cli.wall_thickness, args_cli.patio_width, args_cli.patio_wall_height],
        color=[0.20, 0.78, 0.44],
    )
    patio_west = DynamicCuboid(
        prim_path="/World/Patio/MuroOeste",
        name="patio_muro_oeste",
        position=[patio_center_x - patio_half_l, 0.0, patio_wall_z],
        scale=[args_cli.wall_thickness, args_cli.patio_width, args_cli.patio_wall_height],
        color=[0.20, 0.78, 0.44],
    )

    scene_primitives = [
        entrada_wall_left,
        entrada_wall_right,
        planta_obstacle_a,
        planta_obstacle_b,
        patio_north,
        patio_south,
        patio_east,
        patio_west,
    ]

    for primitive in scene_primitives:
        world.scene.add(primitive)
        _configure_collision_and_kinematic(primitive, UsdPhysics)

    world.reset()
    print("@OUTPUT: Escena UADE cargada con primitivas cinemáticas y colisionadores activos")

    try:
        for _ in range(max(1, args_cli.steps)):
            if not simulation_app.is_running():
                break
            world.step(render=True)
    finally:
        simulation_app.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())