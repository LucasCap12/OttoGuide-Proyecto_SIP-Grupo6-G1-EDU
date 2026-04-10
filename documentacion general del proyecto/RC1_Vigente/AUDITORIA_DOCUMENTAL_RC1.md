# Auditoria Documental RC1 - OttoGuide MVP

## Contexto de contraste

Estado objetivo contrastado:

- Proyecto: OttoGuide MVP
- Hardware target: Unitree G1 EDU 8
- Arquitectura: FastAPI async + ROS 2 + CycloneDDS Unicast + SPA Vanilla JS
- Estado: RC1_LOCKED
- Meta operacional: HIL en entorno real UADE

## Resultado de auditoria

| Archivo auditado | Estado | Hallazgo | Accion recomendada |
|---|---|---|---|
| codigo ottoguide/README.md | Vigente | Describe HIL, arquitectura por capas y flujo operativo actual | Mantener como README tecnico del modulo codigo |
| codigo ottoguide/deploy/RUNBOOK.md | Vigente | Secuencia RC1 de freeze, deploy y activacion systemd | Mantener como runbook de release |
| codigo ottoguide/tests/integration/README.md | Mixto | Incluye HIL real, pero conserva guia de simulacion | Mantener; etiquetar bloque sim como referencial |
| codigo ottoguide/README_SITL_3D.md | Referencial (fase previa) | Documento centrado en topologia SITL 3D pre-HIL | Conservar como historico, no usar como base operativa |
| documentacion general del proyecto/docs/HIL_TESTING_PROTOCOL.md | Vigente | Protocolo de seguridad y operacion HIL detallado | Mantener y versionar contra RC siguientes |
| documentacion general del proyecto/docs/ROS2_INTEGRATION.md | Vigente | Define frontera ROS2/SDK y restricciones de capa | Mantener, sincronizar con cambios de bridge |
| documentacion general del proyecto/docs/MEMORIA_ARQUITECTONICA_MVP.md | Vigente con notas historicas | Memoria robusta; incluye secciones de evolucion y cierre de deuda | Mantener como memoria formal |
| documentacion general del proyecto/docs/Investigacion.md | Referencial tecnica | Documento de investigacion y base de decisiones | Mantener como anexo de contexto |
| documentacion general del proyecto/docs/README.md | Mixto | Mezcla SITL/HIL y referencias historicas en indice maestro | Reemplazar su uso por carpeta consolidada RC1 |
| documentacion general del proyecto/docs/README_simulation.md | Referencial (fase previa) | Enfoque de simulacion, no estado operativo actual | Conservar como anexo historico |
| documentacion general del proyecto/docs/G1-Manual-de-usuario-Transcripcion.md | Vigente referencial proveedor | Manual operativo de hardware y seguridad base | Mantener como fuente normativa de seguridad |
| documentacion general del proyecto/MEMORIA_TECNICA_EXPORT.txt | Obsoleta parcial | Referencias de rutas legacy fuera de codigo ottoguide/ y archivos removidos | Mantener solo como evidencia historica, no documental viva |
| planificacion/V2/README.md | Administrativo | Describe capa de cronogramas, sin impacto tecnico runtime | Mantener en dominio de planificacion |

## Incongruencias detectadas

1. Coexistencia de carpeta historica con espacios y nueva carpeta canonica sugerida para RC1.
2. Persistencia de documentos SITL que pueden confundirse con la base operativa HIL.
3. Memoria tecnica exportada con paths legacy que no representan topologia actual de ejecucion.

## Decisiones de consolidacion ejecutadas

1. Se crea documentacion_general_del_proyecto/ como fuente documental canonica de RC1.
2. Se centraliza arquitectura operativa y runbook de arranque en documentos dedicados.
3. Se deja trazabilidad explicita de documentos vigentes vs historicos para evitar uso incorrecto.
