#!/bin/sh

: <<'DOC'
@TASK: Instalar y habilitar el servicio systemd de OttoGuide en la Companion PC.
@INPUT: Ejecucion como root via sudo y servicio disponible en /home/unitree/ottoguide/codigo_ottoguide/deploy/ottoguide_mvp.service.
@OUTPUT: Symlink creado en /etc/systemd/system, daemon recargado y servicio habilitado.
@CONTEXT: Script exclusivo del target Ubuntu para automatizar el arranque del MVP.
@SECURITY: Abort a menos que el proceso tenga privilegios de root.
STEP [1]: Verificar privilegios de root y existencia del archivo de unidad.
STEP [2]: Crear el symlink en /etc/systemd/system.
STEP [3]: Recargar systemd y habilitar el servicio.
STEP [4]: Iniciar el servicio y mostrar el comando recomendado para logs.
DOC

set -eu

SERVICE_SOURCE=/home/unitree/ottoguide/codigo_ottoguide/deploy/ottoguide_mvp.service
SERVICE_TARGET=/etc/systemd/system/ottoguide_mvp.service

if [ "$(id -u)" -ne 0 ]; then
  echo "@OUTPUT: ERROR ejecutar este script como root usando sudo"
  exit 1
fi

if [ ! -f "${SERVICE_SOURCE}" ]; then
  echo "@OUTPUT: ERROR no existe el archivo de unidad en ${SERVICE_SOURCE}"
  exit 1
fi

ln -sfn "${SERVICE_SOURCE}" "${SERVICE_TARGET}"
systemctl daemon-reload
systemctl enable ottoguide_mvp.service
systemctl start ottoguide_mvp.service

echo "@OUTPUT: Servicio instalado y habilitado en ${SERVICE_TARGET}"
echo "@CONTEXT: Logs en vivo: journalctl -u ottoguide_mvp.service -f"
