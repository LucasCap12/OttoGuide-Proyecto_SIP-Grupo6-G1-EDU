@TASK: Documentar topologia SITL 3D hibrida para OttoGuide MVP
@INPUT: Stack de simulacion Unitree y puente ROS 2
@OUTPUT: Guia tecnica de ejecucion SITL con dominios DDS y flujo de datos
@CONTEXT: Entorno de validacion pre-HIL con visualizacion 3D y colisiones
@SECURITY: Separacion de dominios DDS y aislamiento de procesos para pruebas deterministas

STEP [1]: Objetivo operativo
Validar locomocion, mensajeria y percepcion con una topologia de tres motores desacoplados.

STEP [2]: Componentes de la topologia
unitree_mujoco
Uso: simulacion fisica rapida para dinamica base y control de movimiento
Dominio DDS recomendado: 1
Ubicacion: libs/unitree_mujoco-main

unitree_ros2
Uso: puente de mensajeria ROS 2 y adaptacion de topicos
Dominio DDS recomendado: 1 en capa de puente
Ubicacion: libs/unitree_ros2-master

unitree_sim_isaaclab
Uso: render 3D, colisiones USD y validacion visual de escenarios complejos
Dominio DDS recomendado: 2 para desacoplar render de control
Ubicacion: libs/unitree_sim_isaaclab-main

STEP [3]: Flujo de datos hibrido
1. unitree_mujoco genera estado fisico y comandos de locomocion en DDS Domain 1.
2. unitree_ros2 consume y publica topicos ROS 2 para control y observabilidad.
3. unitree_sim_isaaclab replica estado en entorno USD para validacion 3D y colisiones.
4. OttoGuide consume estado consolidado via puente ROS 2 sin bloquear el event loop.

STEP [4]: Topologia logica
Control Loop: OttoGuide <-> unitree_ros2 <-> unitree_mujoco
Render Loop: unitree_ros2 <-> unitree_sim_isaaclab
Sincronizacion: timestamps ROS 2 y QoS compatibles con CycloneDDS unicast

STEP [5]: Ejecucion recomendada
1. Exportar variables ROS 2 y CycloneDDS en terminal de simulacion.
2. Ejecutar el smoke test DDS antes de cargar la pila pesada.
   Comando: python scripts/sitl_smoke_test.py --send-zero-move
3. Iniciar unitree_mujoco para estabilizar dinamica base.
4. Iniciar unitree_ros2 para levantar puente de topicos.
5. Iniciar unitree_sim_isaaclab para render 3D y colisiones.
6. Iniciar OttoGuide en modo sim con inyeccion ROBOT_MODE=sim.

STEP [6]: Variables de entorno criticas
ROBOT_MODE=sim
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
CYCLONEDDS_URI=file:///ruta/cyclonedds.xml
ROS_DOMAIN_ID=1 para control y 2 para render cuando se requiera desacople fuerte

STEP [7]: Criterios de salida
1. Navegacion waypoint-to-waypoint sin bloqueo del loop asincorno.
2. Telemetria estable entre puente ROS 2 y simuladores.
3. Coherencia de colisiones USD contra estado fisico en mujoco.
4. Degradacion controlada si falla el motor 3D sin detener control base.
