# OttoGuide MVP

## Contexto Académico
Este repositorio implementa el MVP de OttoGuide para la cátedra Seminario de Integración Profesional UADE 2026. El objetivo operacional es ejecutar visitas autónomas con Unitree G1-EDU en modo HIL, con observabilidad en tiempo real y trazabilidad técnica de misión.

## Arquitectura de Red

### Componentes de red
- Robot Unitree G1-EDU con endpoint de locomoción DDS en 192.168.123.161.
- Companion PC Ubuntu en 192.168.123.164 como nodo de cómputo principal.
- Estación de operación conectada por LAN para dashboard, control y auditoría.

### Middleware y transporte
- DDS: CycloneDDS en Domain 0 para locomoción y canales de estado.
- ROS 2 Humble: procesos externos para Nav2, AMCL, drivers de sensores y bringup.
- API: FastAPI + Uvicorn escuchando en 0.0.0.0 para acceso remoto en red local.

### Flujo de locomoción
1. La FSM emite objetivos de navegación al bridge Nav2.
2. Nav2 consume mapa físico y ejecuta control local sobre la locomoción.
3. La parada de emergencia REST fuerza transición a EMERGENCY y cancelación activa de navegación.

## Topología de Archivos (3 raíces)

### Raíz 1: codigo ottoguide/
Contiene código ejecutable, configuración, scripts de operación, mapas, assets y pruebas del MVP.

### Raíz 2: documentacion general del proyecto/
Contiene memoria técnica, protocolos HIL, integración ROS 2 y documentación de respaldo.

### Raíz 3: planificacion/
Contiene artefactos de planificación del proyecto en versiones iterativas.

## Patrones de Diseño Utilizados

### Strategy
El selector ROBOT_MODE habilita tres estrategias de ejecución de hardware:
- real: integración física con Unitree + DDS.
- sim: integración con simulador.
- mock: validación lógica sin hardware.

### FSM Asíncrona
La máquina de estados opera en cuatro estados operativos:
- IDLE
- NAVIGATING
- INTERACTING
- EMERGENCY

Las transiciones se ejecutan con AsyncEngine y tareas no bloqueantes para navegación, odometría, telemetría y auditoría.

## Pipeline HIL (Livox, AMCL, Nav2)

### Sensores y percepción de entorno
- Livox MID360 para nube de puntos y percepción espacial.
- Intel RealSense para soporte visual y referencia adicional.

### Localización
- AMCL consume el mapa físico generado en laboratorio y mantiene pose global 2D.

### Navegación
- Nav2 recibe objetivos secuenciales desde la FSM.
- El bridge de navegación aplica ejecución asíncrona y cancelación segura.
- En emergencia se ejecuta cancelación de navegación y amortiguación de hardware.

## Pipeline de Interacción (STT, Qwen2.5 local, TTS)

### Entrada de voz
- STT local para captura de intención del operador o visitante.

### Inferencia local
- Modelo Qwen2.5 ejecutado en Ollama local sobre la Companion PC.
- Nodo F configurado para interacción llm_qa y respuesta contextual.

### Salida de voz
- TTS local para síntesis auditiva de respuesta.

### Concurrencia
- Las llamadas de audio y LLM se ejecutan de forma asíncrona para no bloquear el Event Loop principal.

## Operación mínima recomendada
1. Sincronizar código en la Companion PC con scripts de despliegue.
2. Ejecutar health check antes del arranque operativo.
3. Levantar servicio systemd del orquestador E2E.
4. Supervisar telemetría por WebSocket y dashboard web.
5. Activar kill switch REST ante condición insegura.