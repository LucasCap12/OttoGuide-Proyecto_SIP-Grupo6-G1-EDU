# Tests de integracion — OttoGuide

Los tests de integracion requieren:

- **ROBOT_MODE=sim**: unitree_mujoco corriendo en domain 1
  ```bash
  cd /ruta/a/unitree_mujoco
  python3 simulate.py g1/scene_29dof.xml
  ROBOT_MODE=sim pytest tests/integration/ -v
  ```

- **ROBOT_MODE=real**: robot fisico G1 EDU 8 conectado
  ```bash
  ROBOT_MODE=real ROBOT_NETWORK_INTERFACE=eth0 pytest tests/integration/ -v
  ```

**No corren en CI/CD.** Ejecutar manualmente antes de cada deploy.

## Advertencia

Estos tests controlan actuadores fisicos del robot (modo real) o
del simulador (modo sim). Verificar que el area esta despejada
antes de ejecutar en modo real.
