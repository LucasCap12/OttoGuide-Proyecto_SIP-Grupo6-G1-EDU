# OttoGuide — Modo Simulacion (ROBOT_MODE=sim)

Instrucciones para ejecutar OttoGuide contra el simulador MuJoCo
en lugar del robot fisico Unitree G1 EDU 8.
Auditado contra el contenido real de `codigo ottoguide/libs/unitree_mujoco-main/`.

Directorio de trabajo recomendado para todos los comandos de esta guía:

```bash
cd codigo ottoguide
```

## Requisitos previos

- Python 3.10+
- MuJoCo instalado:
  ```bash
  pip install mujoco
  ```
- SDK de Python instalado (modo offline desde libs local):
  ```bash
  pip install -e ".[hardware]"
  ```
  Esto instala `libs/unitree_sdk2_python-master` sin acceso a internet.
- Dependencia opcional de joystick (si se usa gamepad):
  ```bash
  pip install pygame
  ```

## Configurar el simulador para G1 (29 DOF)

Antes de lanzar el simulador Python, editar el archivo de configuracion:

```
codigo ottoguide/libs/unitree_mujoco-main/simulate_python/config.py
```

Modificar las siguientes variables:

```python
ROBOT = "g1"
ROBOT_SCENE = "../unitree_robots/g1/scene_29dof.xml"
DOMAIN_ID = 1    # Distinguir del robot real (default 0)
INTERFACE = "lo" # Loopback para simulacion local
ENABLE_ELASTIC_BAND = False
USE_JOYSTICK = 0  # Cambiar a 1 si se dispone de gamepad
```

La escena XML auditada para G1 EDU 8 (29 DOF) se encuentra en:
```
codigo ottoguide/libs/unitree_mujoco-main/unitree_robots/g1/scene_29dof.xml
```
**No usar** `scene_23dof.xml` — corresponde a la variante de 23 DOF.

## Levantar el simulador

```bash
cd libs/unitree_mujoco-main/simulate_python
python3 unitree_mujoco.py
```

El visor MuJoCo se abrira con el robot G1 cargado.
Verificar en la consola que el bridge DDS se inicializa en `DOMAIN_ID=1` sobre interfaz `lo`.

## Iniciar OttoGuide en modo sim

En una terminal separada (mantener el simulador corriendo):

```bash
cd codigo ottoguide
ROBOT_MODE=sim bash scripts/start_robot.sh
```

O directamente:

```bash
cd codigo ottoguide
ROBOT_MODE=sim python main.py
```

El adaptador `UnitreeG1SimAdapter` se conectara via `ChannelFactoryInitialize(1, "lo")`.

## Diferencias con hardware real

| Parametro          | Simulacion (`sim`)            | Hardware real (`real`)          |
|--------------------|-------------------------------|----------------------------------|
| DOMAIN_ID          | 1                             | 0                                |
| INTERFACE          | `lo` (loopback)               | `eth0` u otra interfaz fisica    |
| Script de arranque | `simulate_python/unitree_mujoco.py` | Encendido manual del robot  |
| Escena XML         | `unitree_robots/g1/scene_29dof.xml` | Mundo fisico real          |
| Nav2/AMCL          | Requiere mapa SLAM externo    | Requiere mapa SLAM externo       |
| Velocidad max      | 0.3 m/s (clamping en adapter) | 0.3 m/s (clamping en adapter)   |
| Damp timeout       | 1.5s hard limit               | 1.5s hard limit                  |
| `stand()` FSM ID   | `Start()` = SetFsmId(200)     | `Start()` = SetFsmId(200)        |

## Limitaciones conocidas

- **Escenas de interiores**: `unitree_mujoco` no incluye escenas de
  interiores universitarios. La geometria de las 3 zonas de UADE
  (entrada/planta baja/patio) debe construirse manualmente editando
  o extendiendo `unitree_robots/g1/scene_29dof.xml`.

- **Navegacion**: Nav2/AMCL requiere un mapa SLAM independiente del
  simulador. El simulador provee dinamica del robot, no mapas.

- **Modo Python del simulador**: El simulador Python
  (`simulate_python/`) soporta actualmente solo desarrollo low-level
  (`LowCmd`/`LowState`). El control de alto nivel (LocoClient) via
  DDS domain 1 esta soportado por `UnitreeG1SimAdapter`.

- **GPU / Isaac Lab**: `codigo ottoguide/libs/unitree_sim_isaaclab-main/` esta presente
  en el repositorio pero requiere GPU NVIDIA para entrenamiento RL.
  Queda fuera del alcance del MVP en la Companion PC (Ubuntu, sin GPU).
