@TASK: Runbook final de despliegue SRE para Release Candidate RC1 del MVP OttoGuide.
@INPUT: Repositorio local saneado, lockfile productivo y acceso SSH a Companion PC.
@OUTPUT: Flujo operativo completo desde freeze local hasta activacion del servicio en target.
@CONTEXT: Procedimiento oficial para operacion HIL UADE 2026.
@SECURITY: Mantener ejecucion secuencial estricta y usar sudo solo en la etapa de systemd.

STEP [1]: Congelamiento local en la estacion de desarrollo.

```sh
bash scripts/pre_deploy_cleanup.sh
bash scripts/freeze_dependencies.sh
bash scripts/cut_release.sh
```

STEP [2]: Sincronizacion del codigo al target.

```sh
bash scripts/deploy_to_companion.sh unitree 192.168.123.164 /home/unitree/ottoguide/codigo_ottoguide
```

STEP [3]: Aprovisionamiento en Companion PC.

```sh
ssh unitree@192.168.123.164
cd /home/unitree/ottoguide/codigo_ottoguide
bash scripts/bootstrap_target.sh
```

STEP [4]: Health check y activacion del servicio.

```sh
python3 /home/unitree/ottoguide/codigo_ottoguide/scripts/sre_health_check.py
cd /home/unitree/ottoguide/codigo_ottoguide
sudo ./scripts/install_systemd_service.sh
journalctl -u ottoguide_mvp.service -f
```