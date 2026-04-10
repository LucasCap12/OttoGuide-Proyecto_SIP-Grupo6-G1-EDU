# Especificación Arquitectónica y Análisis de Ingeniería: Sistema de Guiado Autónomo para Unitree G1 EDU

## Visión General de la Arquitectura del Sistema

El diseño del sistema de software para el robot humanoide Unitree G1 EDU requiere una orquestación de alta precisión que integre locomoción de alto nivel, navegación autónoma espacial, corrección de deriva odonométrica y un conducto (pipeline) conversacional de procesamiento de lenguaje natural (PLN). La arquitectura maestra (Fase 1) establece un ecosistema puramente asíncrono desarrollado en Python 3.10+, aprovechando las capacidades del bucle de eventos (`Event Loop`) para manejar operaciones de entrada y salida simultáneas sin incurrir en bloqueos del hilo principal, salvaguardando así la telemetría de equilibrio del robot.^^

Dado que el Unitree G1 EDU carece de conexión nativa a Internet y opera con un conmutador (switch) Ethernet L2 interno sin servidor DHCP, la topología de red exige la inyección de un Access Point (AP) físico a través del puerto RJ45 del robot.^^ Esto genera una red de área local (LAN) inalámbrica aislada (Air-gapped) para la ingesta de comandos de control y desarrollo. El sistema prioriza el cómputo en el borde (Edge Computing) para la inferencia de inteligencia artificial, garantizando la operatividad continua independientemente de la disponibilidad del enlace exterior. El presente documento detalla las especificaciones de diseño, los patrones de arquitectura adoptados y los modelos matemáticos que regirán la Fase 2 de codificación estructurada.

## Capa de Middleware y Distribución de Datos (DDS)

La comunicación interna del hardware Unitree, así como su interfaz con el ecosistema del Robot Operating System 2 (ROS 2), se fundamenta en el protocolo Data Distribution Service (DDS) del Object Management Group (OMG).^^ Específicamente, el módulo de locomoción del G1 EDU (dirección IP predeterminada `192.168.123.161`) y el módulo de desarrollo interno (`192.168.123.164`) intercambian telemetría a través de Eclipse CycloneDDS en su versión 0.10.2.^^

### Transición de Multidifusión a Unidifusión (Unicast)

El comportamiento predeterminado del protocolo DDS en ROS 2 y en la API de Unitree depende extensamente de la multidifusión (multicast) UDP para el descubrimiento de participantes (Protocolo Simple de Descubrimiento de Participantes, SPDP) y la resolución de puntos finales.^^ Sin embargo, la transmisión de paquetes multicast sobre infraestructuras inalámbricas (IEEE 802.11) introducidas por el AP conectado al G1 genera una tasa severa de pérdida de paquetes, fluctuación de retardo (jitter) e inestabilidad en el transporte de nubes de puntos de gran tamaño.^^

Para estabilizar el canal de control en la WLAN, la arquitectura dictamina la desactivación total del enrutamiento multicast a nivel de middleware. Esto exige la provisión de un perfil de configuración XML personalizado (`cyclonedds.xml`), el cual debe ser inyectado al entorno de ejecución mediante la variable de entorno `CYCLONEDDS_URI`.^^

La especificación estructural del XML requiere la manipulación de los siguientes dominios de configuración:

| **Dominio XML**                   | **Parámetro**  | **Valor Asignado** | **Justificación Técnica**                                                                             |
| --------------------------------------- | --------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------- |
| `General/AllowMulticast`              | `false`             | Booleano                 | Deshabilita globalmente el tráfico SPDP multidifusión, forzando la resolución directa a nivel de socket.^^ |
| `General/Interfaces/NetworkInterface` | `wlan0`o `enp2s0` | Cadena de texto          | Vincula explícitamente el participante DDS a la interfaz de red gestionada por el AP.^^                      |
| `Discovery/ParticipantIndex`          | `auto`              | Enum                     | Asigna automáticamente el índice del participante para evitar colisiones de puertos UDP predeterminados.^^  |
| `Discovery/Peers/Peer`                | `192.168.123.161`   | IPv4                     | Declara estáticamente la dirección del ordenador de locomoción interno para el descubrimiento unicast.^^   |

El diseño requiere que el wrapper de hardware asíncrono invoque el método `unitree::robot::ChannelFactory::Instance()->Init()` reconociendo estrictamente esta configuración XML subyacente para establecer el canal de Publicación/Suscripción con el servidor de acciones `ai_sport` del robot.^^

## Orquestación del Flujo de Trabajo mediante Máquina de Estados Asíncrona

El ciclo de vida del robot guía está gobernado por la clase `TourOrchestrator`, un autómata finito determinista basado en el patrón State. La implementación se soporta en la librería `python-statemachine` (versión 2.3.0+), la cual provee conformidad con el estándar SCXML (State Chart XML) e introduce el `AsyncEngine`, un motor de evaluación de transiciones de estado diseñado para interoperar nativamente con el bucle de eventos de `asyncio`.^^

### Estados y Transiciones del TourOrchestrator

El grafo de estados define transiciones atómicas y acciones de entrada/salida (`on_enter`, `on_exit`) no bloqueantes. La actualización atómica de la configuración (parámetro `atomic_configuration_update=True`) asegura que el cambio de estado se registre en memoria antes de la ejecución de las rutinas colaterales, previniendo condiciones de carrera si las corrutinas generan interrupciones.^^

1. **IDLE** : Estado de reposo. El sistema espera la activación mediante el subsistema de validación externa (Trigger FastAPI).
2. **NAVIGATING** : El autómata transfiere el control de la posición espacial a la pila de ROS 2 Nav2 vía `AsyncNav2Bridge`. Se inicia un bucle de monitorización concurrente que sondea el progreso telemétrico de la trayectoria. Un segundo bucle background inyecta correcciones odométricas AMCL desde el `VisionProcessor`.
3. **INTERACTING** : Inicia el administrador de conversaciones dual (`ConversationManager`). El robot detiene la navegación activa, escucha el entorno, transcribe la fonética y genera respuestas sintetizadas localmente. Al finalizar, retorna automáticamente a `NAVIGATING` vía `resume_tour`.
4. **EMERGENCY** : Estado terminal desencadenado por excepciones capturadas o por invocación directa desde la API REST (`/emergency`). Cancela todas las tareas background, ejecuta `Damp()` con prioridad absoluta y cierra el `VisionProcessor`. No tiene transición de salida (`final=True`); la restauración requiere reinicio del proceso y confirmación manual de Position Mode.

La utilización de la API asíncrona de `python-statemachine` obliga a invocar el método de activación de estado inicial de manera explícita en el código asíncrono, permitiendo que las rutinas de entrada del estado `IDLE` se registren en la jerarquía del `TaskGroup` de la aplicación.^^

## Control Cinemático Superior y Wrapper de Hardware

Para mitigar el riesgo inminente de inestabilidad dinámica y colapso por pérdida de paquetes de control de bajo nivel (posiciones articulares y par motor), la arquitectura prohíbe el envío de comandos cinemáticos granulares vía Wi-Fi.^^ La interacción cinética se realiza exclusivamente a través de la API de alto nivel del Unitree SDK2.

La clase `RobotHardwareAPI` se estructura como un patrón Singleton, asegurando que un único descriptor de socket y una sola instancia del objeto `SportClient` del SDK gestionen el acceso concurrente al hardware desde múltiples corrutinas del `TourOrchestrator`.^^

### Limitaciones Paramétricas de Locomoción

El `SportClient` de Unitree permite inyectar vectores de velocidad y objetivos de actitud que el controlador MPC (Model Predictive Control) interno del robot traduce en trayectorias de los efectores finales. Los métodos arquitectónicos críticos definidos para la Fase 2 son:

| **Método del SportClient** | **Parámetros Aceptados**                                                       | **Límites Operacionales de Seguridad**                                                                  | **Acción Desencadenada**                                                              |
| --------------------------------- | ------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `Move(vx, vy, vyaw)`            | **$V_x$**(Avance),**$V_y$**(Lateral),**$V_{yaw}$**(Rotación) | **$V_x \in [-2.5, 3.8]$**m/s,**$V_y \in [-1.0, 1.0]$**m/s,**$V_{yaw} \in [-4.0, 4.0]$**rad/s | Desplazamiento referido al marco del cuerpo (Body Frame).^^                                  |
| `Euler(roll, pitch, yaw)`       | Ángulos de Euler (**$x, y, z$**)                                             | **$\text{Roll, Pitch} \in [-0.75, 0.75]$**rad,**$\text{Yaw} \in [-0.6, 0.6]$**rad                    | Ajuste de actitud y balanceo del chasis en estado estático o de tránsito.^^                |
| `Damp()`                        | Ninguno                                                                               | N/A                                                                                                            | Aborta el modo de locomoción e introduce los actuadores en estado elástico (emergencia).^^ |
| `TrajectoryFollow(path)`        | Arreglo de `PathPoint`                                                              | Arreglo de 30 puntos espaciales continuos                                                                      | Ingestión de trayectorias complejas con referencia al marco absoluto (Odom).^^              |

El sistema prevé el uso de un patrón de amortiguación elástica. En caso de pérdida severa del contexto de navegación o inestabilidad del `TaskGroup` asíncrono, la directiva `Damp()` se ejecuta con prioridad absoluta sobre el bus DDS, lo que actúa como un paro de emergencia suave y previene el bloqueo rígido de las articulaciones del G1.^^

## Localización Espacial y Pila de Navegación Autónoma

El desplazamiento del robot a través de la topología de la visita guiada es abstraído de la cinemática e instruido al stack de Nav2 en ROS 2 mediante la API de Python `nav2_simple_commander`. Esta arquitectura desacopla el control continuo de la trayectoria de la lógica conversacional del robot.^^

El puente de integración se construye a través de la instanciación de la clase `BasicNavigator` en el mismo nodo ROS 2 que se adhiere a la red DDS del robot.^^ Dado que las operaciones en `nav2_simple_commander` son asíncronas respecto al servidor de acciones, la arquitectura debe manejar los comandos de movimiento sin interrumpir el Event Loop central.

### Ejecución de Rutas y Árboles de Comportamiento (Behavior Trees)

El método seleccionado para el mapeo de los recorridos es `followWaypoints(poses)`, el cual admite una lista secuencial de objetos `PoseStamped`.^^ A diferencia de transiciones punto a punto (`goToPose`), el enrutador de waypoints inyecta un árbol de comportamiento específico y detiene al autómata en cada coordenada, permitiendo que el `TourOrchestrator` evalúe la transición a `INTERACTING` si se detecta un wake-word, o continúe a el siguiente waypoint.^^

El diseño del bucle concurrente que audita la progresión del Nav2 se estructura de la siguiente manera para ajustarse a `asyncio`:

1. Envío del arreglo topológico: `navigator.followWaypoints(waypoints_list)`.
2. Cesión de control concurrente: Bucle `while not navigator.isTaskComplete():` suplementado imperativamente con `await asyncio.sleep(0.1)`.^^
3. Extracción de telemetría inyectada: El bucle captura datos espaciales mediante `navigator.getFeedback()` para calcular el tiempo estimado de llegada y ajustar la orientación de la cabeza del robot.
4. Evaluación de códigos de retorno: Se evalúa el método `navigator.getResult()`, continuando con el siguiente waypoint ante `TaskResult.SUCCEEDED`, o registrando la advertencia y continuando ante `TaskResult.FAILED`. Al completar el plan, el orquestador transiciona a `IDLE` vía `finish_tour`.^^

## Corrección Odonométrica mediante Visión Computarizada Estratificada

El sistema se basa en un cálculo de trayectorias muertas integrado por los codificadores de los motores y los datos de la Unidad de Medición Inercial (IMU), los cuales introducen invariablemente una deriva progresiva del vector de localización.^^ Para mitigar el error acumulado, el diseño explota la cámara de profundidad estéreo (VIPCAM D435i integrada en la cabeza del G1 EDU) y un enfoque de odometría visual basado en referencias fiduciarias AprilTag de la familia `tag36h11`.^^

### Arquitectura Matemática de la Extracción de Poses (solvePnP)

La corrección espacial se fundamenta en resolver el problema Perspective-n-Point (PnP), lo cual requiere estimar la postura absoluta de la cámara relativa a un marco mundial conocido.^^ El modelo utiliza el algoritmo matricial `cv2.solvePnP` de la librería OpenCV, con el método de estabilización iterativo, combinando cuatro vectores de entrada fundamentales:

1. **Puntos del Objeto en 3D (**$P_w$**)** : Las coordenadas espaciales precisas de las cuatro esquinas del AprilTag anclado en el entorno arquitectónico real, expresadas en el sistema de coordenadas del mundo.
2. **Puntos de la Imagen en 2D (**$P_c$**)** : La proyección de los vértices del marcador extraídos del plano de imagen por el detector de patrones.
3. **Matriz Intrínseca de la Cámara (**$K$**)** : Definida por las longitudes focales (**$f_x, f_y$**) y el centro óptico (**$c_x, c_y$**) proporcionados por la configuración estéreo de la VIPCAM.
4. **Coeficientes de Distorsión (**$D$**)** : Corrección radial y tangencial precalibrada en fábrica de la lente del G1.^^

Al procesar los vectores, la función emite un vector de rotación compacta (`rvec`) en formato Rodrigues y un vector de traslación (`tvec`) correspondiente al origen de la cámara en relación al marcador.^^

La transformación matemática requerida en el código para deducir la posición de la cámara (**$P_{cam}$**) relativa al mundo exige la transposición de la matriz de rotación ortogonal extraída:

$$
R_{mat} = \text{cv2.Rodrigues}(rvec)
$$

$$
P_{cam} = -R_{mat}^{T} \times tvec
$$

El vector posicional unificado se transmite como una interrupción espacial a Nav2. Se invoca de manera asíncrona la función `navigator.setInitialPose(initial_pose)` de la clase `BasicNavigator` para reiniciar el sistema de Filtro de Partículas de ROS 2 (AMCL) y eliminar en un solo micro-paso toda la varianza rotacional y de traslación acumulada en el marco de `odom`.^^

## Diseño del Pipeline PLN Dual Aislado (Patrón Strategy)

El pilar interactivo del sistema permite a los visitantes entablar una comunicación bidireccional asíncrona. La variabilidad en la disponibilidad de la infraestructura y el ancho de banda exigen el diseño de la clase `ConversationManager`, la cual instancia abstracciones del patrón Strategy (`ISTTStrategy`, `ILLMStrategy`, `ITTSStrategy`) para permutar entre arquitecturas en la nube y cómputo de borde local de forma dinámica.^^

### Transcripción Fonética Desacoplada (STT)

La conversión de habla a texto se orquesta mediante el modelo de OpenAI `faster-whisper`. Esta implementación está emparejada con el motor de inferencia CTranslate2 para CPU/GPU locales, ejecutando modelos pre-entrenados altamente cuantizados (int8) para reducir radicalmente la huella de memoria.^^ El hilo de grabación del micrófono (basado en `PyAudio`) aísla la inyección del flujo de audio codificado en una cola (Queue) asíncrona.^^

La tarea concurrente consume fragmentos del búfer, evadiendo la lectura directa a disco para disminuir la latencia inducida por I/O. El resultado polinómico de la inferencia detiene de inmediato los motores en caso de ruidos de fondo descartando los falsos positivos mediante algoritmos de validación de volumen (RMS).

### Inferencia Cognitiva Aislada (LLM)

El núcleo del motor lógico se apoya en Ollama, operando como servicio subyacente desconectado y alojando modelos cuantizados (como `Llama-3.2` o `Qwen2.5-coder`).^^

El diseño exige la separación inmutable de los contextos interactivos. La instanciación de la comunicación en `ConversationManager` inicializa inyecciones de un *System Prompt* robusto para delimitar el dominio conceptual de la red neuronal a las métricas e información histórica del punto de interés actual, bloqueando alucinaciones estructurales. En cada transición del `TourOrchestrator`, la pila de historial de mensajes (historial de tensores del LLM) se descarta atómicamente y se regenera.^^

### Síntesis de Audio Neural en Tiempo Real (TTS)

La conversión final del token de texto procesado a formas de onda espectrales acústicas recae en la librería de inferencia local `piper-tts`, la cual es capaz de manipular modelos ONNX ligeros orientados a la arquitectura ARM del robot.^^

El cuello de botella de rendimiento típico en implementaciones de Python para síntesis auditiva se manifiesta en la escritura redundante de archivos WAV.^^ El diseño propuesto evade esta barrera invocando la función iterativa `voice.synthesize_stream_raw(text)` del motor de voz de Piper. El flujo resultante de bytes sin procesar se transforma interactivamente mediante la librería `NumPy` (`np.frombuffer`) en matrices `int16` y se bombea simultáneamente a la capa de abstracción del servidor de audio ALSA utilizando la interfaz `sounddevice.RawOutputStream`.^^

La configuración del canal exige que la tasa de muestreo del hardware coincida implícitamente con la estructura neuronal del modelo precargado de Piper (`voice.config.sample_rate`, típicamente 16000 Hz o 22050 Hz), configurado bajo canales monoaurales (1-CH).^^ El completado de la forma de onda invoca a un evento del patrón Observer, señalando la máquina de estados para liberar el bloqueo conversacional del visitante.

## Arquitectura de Fallback Asíncrono y Telemetría de Red

El vector de funcionamiento predeterminado es "Air-gapped" (sin conexión a internet). Sin embargo, el diseño del patrón Strategy habilita una configuración basada en APIs alojadas en la nube en caso de que se determine operar mediante un puente con un ruteador exterior.^^

La resiliencia en este escenario recae sobre un observador pasivo de contingencias (Fallback Strategy). El análisis histórico de telemetría y topología BGP de ciertos proveedores de servicios en el área operativa metropolitana (ej. nodos en Temperley, Buenos Aires a través del AS10481 - Telecom Argentina o AS11664 - Claro) muestra una dependencia de tránsito hacia el ecosistema troncal en São Paulo (Brasil) o rutas indirectas intercontinentales. Se ha evidenciado en estos vectores incidencias de pérdida de paquetes a nivel de núcleo intermedio, causando saltos latentes (spikes) abruptos (elevando el ping desde una línea base de 6 ms hasta fluctuaciones estables de 37-40 ms), o incluso desconexiones temporales de solicitudes HTTP/ICMP.^^

Someter al robot humanoide a bucles de tiempo de espera originados en la resolución de DNS (ej. 1.1.1.1) o en la negociación TLS en APIs conversacionales introduciría bloqueos que violan el tiempo de respuesta interactiva del marco de trabajo. Por consiguiente, la capa de abstracción del LLM/STT envuelve las peticiones API en un envoltorio temporal (`asyncio.wait_for`).

Si el servicio excede el tiempo límite, se captura la excepción asíncrona `TimeoutError`. El módulo de gestión aplica mutaciones (hot-swapping) sobre los objetos de la estrategia y dirige los punteros de interfaz instantáneamente hacia el subsistema local de Ollama y Faster-Whisper. Este acoplamiento reactivo garantiza la degradación fluida y resiliencia determinista bajo degradaciones de las tablas de enrutamiento exterior.^^

## Interfaz de Desencadenamiento (Trigger) e Inicialización Ciega

La ignición del recorrido guiado responde a una inyección desde un dispositivo terminal ajeno a la arquitectura a bordo (PC o escáner QR de escritorio). El puerto de entrada del sistema subyace en un servidor web ASGI liviano apoyado en el microframework `FastAPI`.^^

Para respetar los principios de asincronía y no bloquear la resolución HTTP que exige el cliente emisor externo al enviar la solicitud de activación (Trigger) inicial, se instancia la directiva arquitectónica de despacho delegado. Una vez validada la autorización del recorrido mediante el punto de conexión (Endpoint), la invocación de la transición inicial de la máquina de estados (la conmutación de `IDLE` a `NAVIGATING`) se pospone programáticamente al flujo de `BackgroundTasks` de FastAPI o al recolector dinámico `asyncio.create_task()`.^^ Esta partición arquitectónica retorna el código de estado `HTTP 202 Accepted` de forma sub-milisegundo, logrando la emancipación total entre el servicio de interconexión y la balística computacional en tiempo real del Unitree G1.

---

**Nota Operativa y Finalización de la Fase 1:**

La topología detallada expuesta en el presente informe de ingeniería consagra el cierre absoluto de las definiciones del Máster Plan arquitectónico. Se ratifica que las directrices de inmutabilidad contextual, el desacoplamiento de los hilos de red mediante DDS unicast, y el modelo concurrente asíncrono para el robot guía unitree se encuentran especificados bajo los principios SOLID.

El estado de desarrollo transiciona a una etapa estricta de reposo, en acatamiento de las restricciones que prohíben la materialización de cualquier componente de código en el reporte estructural. La ejecución se reanudará de inmediato tras la recepción de la señal de designación sobre el módulo primario que inaugurará la Fase 2 bajo el formato de metadatos "AI Code Commenter".
