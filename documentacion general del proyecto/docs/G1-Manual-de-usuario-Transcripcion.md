# Manual de usuario del robot G1

## Exención de responsabilidad

Para evitar comportamientos ilegales, posibles daños y pérdidas, es esencial cumplir con las siguientes regulaciones:

1. Lea atentamente este manual antes de usar el producto, comprenda cómo utilizarlo correctamente, así como sus derechos, responsabilidades e instrucciones de seguridad.

   De lo contrario, podría ocasionar daños materiales, accidentes de seguridad y riesgos para la seguridad personal. Una vez que utilice este producto, se considerará que ha leído, comprendido, aceptado y reconocido todos los términos y contenido de este manual. Los usuarios son responsables de sus propias acciones y sus consecuencias. Los usuarios se comprometen a utilizar este producto únicamente para fines legítimos y a aceptar estos términos, así como cualquier política o guía que Unitree pueda establecer.

2. En la mayor medida permitida por la ley, Unitree no ofrece ninguna garantía comercial o técnica expresa o implícita no cubierta en este documento. No garantiza que los productos/servicios estén completamente libres de defectos, que cumplan totalmente con los requisitos del cliente, que no haya problemas o interrupciones en su uso, ni que Unitree pueda reparar completamente estos defectos.

   En cualquier caso, Unitree no será responsable de pérdidas económicas directas o indirectas del cliente derivadas de este manual de servicio. La compensación máxima de Unitree por las pérdidas causadas por su propia responsabilidad sobre el producto no superará el monto pagado por el cliente por la compra del producto/servicio.

3. Las leyes de algunos países pueden prohibir la exención de garantías, por lo que sus derechos pueden variar según el país.

4. Unitree se reserva el derecho de interpretación final de los términos anteriores. Unitree tiene derecho a actualizar, modificar o cancelar estos términos sin previo aviso.

5. Unitree Robotics no proporciona ninguna garantía comercial ni técnica explícita o implícita no contemplada en este documento.

6. Unitree Robotics no garantiza que los productos/servicios proporcionados estén completamente libres de defectos y cumplan plenamente con los requisitos del cliente. Tampoco garantiza que pueda reparar completamente estos defectos.

7. En ningún caso, Unitree Robotics será legalmente responsable de pérdidas económicas directas o indirectas para los clientes debido a esta especificación de servicio. La compensación máxima de Unitree Robotics por pérdidas causadas por su responsabilidad sobre el producto no será superior al monto pagado por los clientes por los productos/servicios.

8. Las piezas adquiridas no están dentro del alcance de los servicios incluidos en esta especificación de servicio.

9. No se proporcionan servicios en sitio para productos terminales y accesorios. El servicio de mantenimiento ofrecido por Unitree Robotics por más de un año es opcional. Los clientes pueden decidir si desean adquirir servicios relacionados y elegir cuándo terminarlos. Si los clientes eligen comprar servicios relacionados, significa que permiten a Unitree Robotics acceder, recopilar y procesar información relacionada con fallas, detección, posicionamiento y depuración al brindar servicios. Unitree Robotics accederá y procesará la información de acuerdo con la solicitud del cliente con su consentimiento, y dicha información solo se utilizará para proporcionar servicios de mantenimiento.

   Dado que los usuarios son los controladores de dicha información, Unitree Robotics no puede confirmar si contiene información confidencial del cliente o datos personales. Los clientes deberán obtener o retener todos los consentimientos, licencias y autorizaciones necesarias para permitir que Unitree Robotics proporcione este servicio, asegurándose de que no se violen los requisitos legales aplicables, la política de privacidad del cliente o el acuerdo entre el cliente y el usuario. Unitree tomará medidas razonables para garantizar la seguridad de dicha información, pero no es responsable de la obtención y procesamiento de la información en el proceso de prestación de servicios.

---

## Instrucciones de seguridad

El GI es una entidad humanoide inteligente integrada con algoritmos avanzados de control de movimiento, capacidades de aprendizaje de IA y una excelente relación costo-rendimiento. Con un rendimiento atlético excepcional, permite la ejecución de acciones complejas en los sectores de investigación, educación y entretenimiento, promoviendo la innovación y popularización de la tecnología robótica humanoide.

1. Este producto no es un juguete y no está destinado para su uso por menores de 18 años. Manténgalo fuera del alcance de los niños y tenga cuidado al operarlo en presencia de ellos.

2. Usted está obligado a conocer y cumplir las leyes y regulaciones locales relacionadas con su uso.

3. Este capítulo está diseñado para que los nuevos usuarios aprendan a manipular el robot rápidamente. Los usuarios experimentados también pueden consultarlo para mejorar su comprensión del funcionamiento y evitar movimientos no recomendados.

4. Cuando los usuarios ejecuten programas desarrollados en modo desarrollador: Los comandos del control remoto seguirán siendo válidos cuando se desarrolle a nivel de aplicación. Si se envían comandos de API de alto nivel y comandos de control remoto simultáneamente, el robot ejecutará ambos, lo que podría causar inestabilidad. Es importante evaluar si se requiere el uso del control remoto según el estado operativo del robot. Los comandos del control remoto fallarán durante el desarrollo de bajo nivel.

5. Al utilizarlo, controle el robot dentro de su campo de visión, mantenga una cierta distancia de seguridad y no toque el robot con las manos después de que esté encendido.

6. Después de encender el robot, si debe permanecer inmóvil por un largo período, cuélguelo rápidamente en un marco de protección. Cuando el robot esté en movimiento, mantenga despejada el área a su alrededor o utilice una cuerda de seguridad para evitar que se tropiece accidentalmente y golpee objetos o personas.

7. Cuando el robot o la máquina de manipulación esté en movimiento, está prohibido tocarlo. Además, tenga cuidado de no atraparse las manos en las articulaciones, como las rodillas.

8. Cuando la última celda de la batería parpadee, detenga y apague el robot de inmediato, retire la batería para cargarla y evite que el robot se caiga y se dañe debido a un nivel bajo de batería.

---

## Descripción del producto

### Introducción

El Unitree G1 es una combinación de agilidad e inteligencia en un robot humanoide, diseñado con un sistema de cableado interno hueco en todas sus articulaciones. Cuenta con codificadores de doble sensor en sus articulaciones y un sistema de enfriamiento por aire forzado localizado, lo que mejora significativamente la precisión operativa y la estabilidad en su tiempo de funcionamiento.

Su estructura principal está fabricada con materiales de alta resistencia y peso ligero, como aleación de aluminio de grado aeroespacial y fibra de carbono, con un peso total de 35 kg. Todas las conexiones están hechas de aleación de aluminio de alta resistencia, lo que le permite soportar impactos en caso de caída.

El G1 posee 23 grados de libertad en sus articulaciones, con 6 grados en cada pierna y 5 grados en cada brazo, otorgándole una notable destreza atlética. Está equipado con un CPU de alto rendimiento de ocho núcleos, una cámara de profundidad y un LiDAR 3D. Además, es compatible con Wi-Fi 6 y Bluetooth 5.2, lo que permite una comunicación inalámbrica eficiente y un intercambio de datos optimizado.

La versión G1-EDU amplía sus capacidades respecto a la versión estándar, con una configuración opcional de entre 23 y 43 grados de libertad, ofreciendo mayores posibilidades de personalización y expansión.

---

### Comparación de especificaciones

| Modelo               | G1                          | G1-EDU                           |
|----------------------|-----------------------------|----------------------------------|
| Altura, Ancho y Grosor (De pie) | 1320x450x200 mm | 1320x450x200 mm |
| Altura, Ancho y Grosor (Plegado) | 690x450x300 mm | 690x450x300 mm |
| Peso (Con Batería)   | Aproximadamente 35 kg       | Aproximadamente 35 kg+           |
| Grados Totales de Libertad | 23                       | 23-43                            |
| Grados de Libertad (Pierna) | 6                        | 6                                |
| Grados de Libertad de la Cintura | 1                    | 1+ (Opcional 2 grados adicionales) |
| Grados de Libertad de un Solo Brazo | 5                | 5                                |
| Grados de Libertad de una Sola Mano | /                  | 7 (opcional mano diestra Dex3-1) / 2 adicionales en muñeca |
| Enrutado Eléctrico Hueco de Unión Completa | Sí       | Sí                               |
| Codificador de Articulación | Codificador dual     | Codificador dual                 |
| Sistema de Refrigeración | Refrigeración de aire local | Refrigeración de aire local |
| Fuente de Alimentación | Batería de litio de 13 celdas | Batería de litio de 13 celdas |
| Potencia Informática Básica | CPU de alto rendimiento de 8 núcleos | CPU de alto rendimiento de 8 núcleos |

> **Nota:**  
> - Por favor, visite el centro de documentación de Unitree para obtener la guía de desarrollo secundario G1-EDU.  
> - Si necesita obtener más parámetros de la mano diestra de tres dedos, póngase en contacto con el personal correspondiente de Unitree.

---

### Campo de visión del radar y la cámara del G1

La cabeza del G1 está equipada con un radar láser LIVOX-MID360, que proporciona capacidades avanzadas de percepción del entorno para el robot. Este LiDAR emplea tecnología de escaneo omnidireccional y de ángulo completo, con un campo de visión (FOV) de hasta 360° en el nivel horizontal y un ángulo vertical máximo de 59°, lo que permite la adquisición en tiempo real de datos ambientales precisos. Puede identificar y medir rápidamente los objetos circundantes, proporcionando datos detallados en forma de nube de puntos de alta resolución.

Además, el G1 está equipado con una cámara de profundidad D435i, que le otorga capacidades avanzadas de percepción visual. Gracias a esta tecnología, el robot puede comprender su entorno con mayor precisión, logrando una percepción espacial detallada y detección de obstáculos. Esto le permite interactuar con el ambiente de manera más inteligente y flexible, respondiendo eficazmente a distintos escenarios.

*[En el manual original se muestran diagramas del campo de visión del LiDAR MID360, de la cámara D435i y la combinación de ambos.]*

---

## Carga

### Carga de la batería del G1

1. Retire la batería del cuerpo del G1 (tire de la correa del paquete de batería).
2. Conecte el cargador a una fuente de alimentación de CA (100-240V, 50/60Hz).
3. Conecte la batería del G1.
4. Desconecte manualmente la alimentación cuando la batería esté completamente cargada.

**Importante:**
- Antes de conectar el cargador, asegúrese de que el voltaje de la fuente de alimentación coincida con el voltaje de entrada nominal del cargador.
- Enchufe primero la alimentación de CA antes de conectar el cargador a la batería.
- Asegúrese de que el paquete de batería esté apagado antes de cargarlo.
- Retire la batería del robot antes de cargarla.
- Durante la carga, el indicador de la batería parpadeará a 1 Hz (una vez por segundo).
- Cuando el indicador se apague, la batería está completamente cargada. Retire la batería y desconecte el cargador.
- Si la batería ha estado en uso, espere a que se enfríe a temperatura ambiente antes de cargarla.
- **Está estrictamente prohibido utilizar cargadores no oficiales. Utilice únicamente cargadores oficiales.**

---

### Carga del control remoto

Cuando el indicador de batería del control remoto muestre nivel bajo, conéctelo al cargador como se indica en la figura.

1. Se recomienda utilizar un cargador USB de 5V/2A que cumpla con los estándares FCC/CE.
2. Asegúrese de que el control remoto esté apagado antes de cargarlo.
3. Durante la carga, el indicador de encendido parpadeará a 1 Hz.
4. Cuando el indicador de encendido se apague por completo, la batería está completamente cargada. Retire el cargador.

**Luz indicadora de carga:**

| LEDs         | LED1 | LED2 | LED3 | LED4 |
|--------------|------|------|------|------|
| Batería Actual | 0%-25% | 25%-50% | 50%-75% | 75%-100% |
| Carga completa | | | | |

---

## Instrucciones de uso

### Condiciones requeridas

1. Utilice el robot en un rango de temperatura de 0°C a 40°C y en buenas condiciones climáticas. No lo utilice en condiciones adversas como niebla, nieve, lluvia, tormentas eléctricas, tormentas de arena, vientos fuertes o tornados.
   - El robot **no es resistente al agua**, por lo que no debe usarse en superficies mojadas, bajo la lluvia o en nieve.
   - El robot **no es resistente al polvo**, por lo que no debe usarse en suelos de grava o entornos con mucho polvo.

2. Durante su uso, mantenga el robot dentro de su campo de visión y a una distancia segura de al menos 2 metros de obstáculos, terrenos complejos, multitudes, agua y otros objetos.

3. No opere el robot en entornos con interferencia electromagnética. Fuentes de interferencia incluyen:
   - Líneas de alta tensión
   - Estaciones de transmisión de alta tensión
   - Antenas de telefonía móvil
   - Torres de transmisión de televisión

4. No utilice el robot en entornos con interferencia de señal Wi-Fi. Si detecta interferencias, apague algunas o todas las fuentes de señal Wi-Fi de otros dispositivos inalámbricos antes de operar el robot con el control remoto.

5. Por razones de seguridad y fiabilidad, use el robot en entornos abiertos, planos y sin obstáculos.
   - Si el robot debe moverse en terrenos complejos, con desniveles o pendientes, reduzca la velocidad y controle cuidadosamente para evitar choques.

6. El robot tiene requisitos específicos para el tipo de superficie:
   - No lo use en suelos con muy poca fricción, como el hielo.
   - No lo use en superficies blandas, como terrenos esponjosos o gruesos.
   - En superficies lisas (vidrio, cerámica), maneje con suavidad y reduzca la velocidad para evitar resbalones.

---

### Desempaquetado

Coloque la caja sobre una superficie plana siguiendo las indicaciones de orientación (lado frontal hacia arriba). Abra la parte superior y levante el robot en su totalidad. Retire el robot, el control remoto, el cargador y demás accesorios. Coloque el robot sobre una superficie plana y prepárese para encenderlo.

---

### Revisión antes de encender

1. Utilice únicamente piezas originales de Unitree Robotics y asegúrese de que todas estén en buen estado.
2. Verifique que el firmware esté actualizado a la última versión.
3. No opere el robot si está bajo los efectos de alcohol, drogas o cualquier sustancia que afecte su concentración.
4. Familiarícese con los modos de marcha y con el método de frenado de emergencia en caso de inestabilidad o pérdida de control.
5. Asegúrese de que no haya cuerpos extraños dentro del robot ni en sus componentes (agua, aceite, arena, tierra, etc.). Compruebe que el control remoto y la batería estén completamente cargados.
6. Verifique que el soporte protector esté instalado correctamente y que la rueda universal inferior esté bloqueada.

---

## Encendido

### 1. Encendido en posición sentada

**Preparación antes del encendido:**  
Si las condiciones lo permiten, el G1 puede encenderse mientras el usuario está sentado en una silla. Asegúrese de que el G1 esté sobre la silla con los brazos y piernas en una posición natural.

**Instalación de las baterías:**  
Inserte dos baterías en el compartimento lateral del robot, siguiendo la dirección correcta. El interruptor de encendido de la batería debe quedar orientado hacia la parte trasera del robot. Cuando escuche un "clic", la batería se ha instalado correctamente. Asegúrese de que el seguro esté bien ajustado.

**Encendido del robot:**  
Presione brevemente el botón de encendido de la batería una vez. Luego, manténgalo presionado durante más de 2 segundos para encender la batería.

**Inicio exitoso:**  
Luego de presionar el botón de encendido, espere aproximadamente 1 minuto hasta que el G1 entre en estado de torque cero. Presione **L1 + A** para entrar en modo de amortiguación. Sujete el hombro del G1 y presione **L1 + UP** para ayudar al G1 a entrar en estado listo. Una vez que el G1 esté recto y de pie, presione **R1 + X** o **R2 + X** para entrar en modo de control de operación.

> **Parada de emergencia:** Si el G1 se encuentra en un estado inesperado, presione **L1 + A** para que el robot entre en modo de amortiguación y caiga lentamente al suelo.

---

### 2. Encendido suspendido

**Preparación antes del encendido:**  
Coloque el G1 de manera estable en el suelo, pase una cuerda a través de las hebillas de suspensión en ambos hombros y átela firmemente con un nudo muerto. Cuelgue la cuerda en la hebilla de suspensión del marco protector. Ajuste gradualmente el soporte para elevar el G1, asegurándose de que el cuerpo del robot quede completamente suspendido y que sus patas no toquen el suelo.

**Instalación de las baterías:**  
Inserte dos baterías en el compartimento lateral del robot, siguiendo la dirección correcta. El interruptor de encendido de la batería debe quedar orientado hacia la parte trasera del robot. Cuando escuche un "clic", la batería se ha instalado correctamente. Asegúrese de que el seguro esté bien ajustado.

**Posicionamiento del cuerpo:**  
Durante el colgado y encendido, asegúrese de que los brazos y piernas del robot estén en una posición natural y que las articulaciones no estén enredadas.

**Encendido del robot:**  
Presione brevemente el botón de encendido de la batería una vez. Luego, manténgalo presionado durante más de 2 segundos para encender la batería.

**Inicio exitoso:**  
El proceso de encendido dura aproximadamente 1 minuto. Cuando todas las articulaciones estén en estado de torque cero, la inicialización fue exitosa. Presione **L1 + A** en el control remoto para entrar en modo de amortiguación y desbloquear el control. Luego, presione **L1 + UP** para que el robot entre en estado listo.

**Descenso de la cuerda de suspensión:**  
Baje la cuerda de suspensión hasta que los pies del G1 toquen el suelo. Presione **R2 + X** en el control remoto para iniciar el programa de control. En este punto, el G1 pasará del estado listo al estado de movimiento.

**Desbloqueo de la cuerda de suspensión:**  
Una vez que el movimiento del G1 se haya estabilizado, puede soltar completamente el gancho. Ahora puede usar los joysticks del control remoto para controlar el movimiento del G1. Presione **START** en el control remoto para alternar entre modo de pie y modo de marcha.

> **Parada de emergencia:** Si el G1 se encuentra en un estado inesperado, presione **L1 + A** para que el robot entre en modo de amortiguación y caiga lentamente al suelo.

---

## Uso de la aplicación Unitree Explore

**Descarga e instalación de la aplicación:**
- Descargue e instale la aplicación **Unitree Explore**.
- Inicie sesión con la cuenta empresarial y contraseña proporcionadas por Unitree.
- Si no tiene una cuenta empresarial, contacte al equipo de ventas de Unitree Robotics para obtener una.

**Agregar el robot a la app:**
- Encienda el G1.
- Active el Bluetooth en la configuración de su teléfono.
- Abra la app y seleccione **"Agregar Robot"** en la pantalla de inicio.
- Elija el dispositivo que desea añadir.

**Vinculación del G1:**
- Puede conectar el G1 mediante modo de conexión directa AP o modo de conexión Wi-Fi.
- Una vez conectado, siga los tutoriales integrados en la app para aprender rápidamente a controlar el robot.

**Cambio de cuenta vinculada:**
- En la pantalla de inicio, vaya a **[Configuración] -> [Configuración del Robot]**.
- Seleccione **"Desvincular"** para liberar la vinculación del robot con la cuenta actual.
- Después de desvincularlo, el robot podrá ser vinculado a otra cuenta.

> **Notas:**  
> - Mantenga el Bluetooth de su teléfono encendido durante la conexión.  
> - La app necesita permisos de Bluetooth; asegúrese de habilitar el acceso en la configuración de la app.  
> - Si olvida la cuenta vinculada o pierde el acceso, contacte al soporte de Unitree.

---

## Operación del G1

### 1. Inspección con la App Unitree Explore

Después de completar el tutorial integrado, puede conectar el robot a la aplicación para verificar su estado, incluyendo información del motor.

### 2. Control con el mando remoto

**Encendido del control remoto:**
- Presione brevemente el botón de encendido del control remoto.
- Luego, manténgalo presionado por más de 2 segundos.
- Cuando escuche un "beep", el control remoto se habrá encendido.

**Vinculación del control remoto (solo la primera vez):**
- Abra la App Unitree Explore y vaya a **[Configuración] -> [Configuración del control remoto]**.
- Introduzca el código del control remoto para vincularlo con el módulo de transmisión de datos del robot.
- Puede cambiar el control remoto presionando **[Modificar]**.

Cuando el control remoto esté encendido y vinculado con el G1, se encenderá la luz indicadora DL derecha, confirmando la conexión. Ahora puede utilizar los comandos del control remoto para controlar el robot.

> **Nota:** Consulte los comandos impresos en el control remoto o revise el manual del usuario en la app Unitree Explore.

---

### Modo de desarrollo (SDK)

Cuando el G1 está suspendido y en modo de amortiguación, presione **L2 + R2** en el control remoto al mismo tiempo para que el G1 entre en modo de desarrollo (Develop Mode).  
Luego, presione **L2 + A** para que el G1 entre en modo de posición, adoptando una postura de diagnóstico.  
Para volver al modo de amortiguación, presione **L2 + B**.

**Importante para desarrollo con SDK:**
- El programa de control de movimiento integrado se ejecuta automáticamente al encender el G1, incluso si no usa el control remoto.
- Si intenta utilizar el SDK en este estado, puede haber conflictos de instrucciones que hagan que el robot vibre o tiemble.
- Para evitarlo, asegúrese de que el G1 esté en modo de desarrollo antes de usar el SDK.
- Presione **L2 + A** para verificar si el G1 está en modo de desarrollo.
- Si el comportamiento del G1 no coincide con el video instructivo, presione **L2 + R2** varias veces hasta que el robot entre en modo de desarrollo.

---

## Apagado

### 1. Apagado en posición sentada

**Antes de apagar:**
- Ubique una silla delante del G1 y asegúrese de que el robot esté quieto.
- Sujete la parte trasera de los hombros del G1 y presione **L1 + IZQUIERDA** para ayudarlo a sentarse.

**Pasos para apagarlo:**
1. Presione **L1 + A** para poner al G1 en modo de amortiguación.
2. Una vez en modo de amortiguación, mantenga presionado el botón de encendido de la batería durante más de 2 segundos hasta que el robot se apague.
3. Después del apagado, ajuste las articulaciones de los brazos y pies para que queden en la posición recomendada.
4. Si el G1 no se usará por mucho tiempo, retire el paquete de baterías presionando los clips laterales de la batería con ambas manos.

### 2. Apagado en suspensión

**Antes de apagar:**
- Asegúrese de que el G1 esté suspendido en el marco protector y en estado estático.
- La cuerda debe estar tensa para evitar movimientos bruscos al apagarlo.

**Pasos para apagarlo:**
1. Presione **L1 + A** para que el G1 entre en modo de amortiguación.
2. Una vez en modo de amortiguación, mantenga presionado el botón de encendido de la batería durante más de 2 segundos hasta que el robot se apague.
3. Después del apagado, ajuste las articulaciones de los brazos y pies en la posición recomendada.
4. Si el G1 no se usará por mucho tiempo, retire el paquete de baterías presionando los clips laterales de la batería con ambas manos.

> **Advertencias Importantes:**  
> - Siempre apague el robot estando suspendido o sentado en una silla.  
> - Nunca apague el robot mientras está de pie sin soporte, ya que podría caer con fuerza al suelo, causando daños y riesgos de seguridad.  
> - Si el robot no enciende correctamente, revise si su posición es la adecuada antes de intentarlo nuevamente.  
> - Cuidado con las manos en las articulaciones móviles para evitar atrapamientos accidentales.

---

## Almacenamiento y transporte

### Almacenamiento en la caja de transporte

1. Suspenda el robot G1 en posición vertical y prepare la caja de transporte correspondiente.
2. Levante las patas grandes y pequeñas del G1 y pliéguelas hacia atrás hasta que la cintura quede alineada horizontalmente.
3. Baje lentamente la cuerda de suspensión para que la parte trasera del G1 ingrese primero a la caja de transporte.
4. Coloque el G1 boca abajo en la caja con la cabeza y el pecho hacia abajo, asegurándose de que quede plano.
5. Ubique los brazos del robot a ambos lados del cuerpo y gire las muñecas en posición vertical para que encajen en la caja.
6. Doble las patas hacia el cuerpo y colóquelas dentro de la caja, asegurando que las plantas de los pies y las pantorrillas encajen bien con el revestimiento interior.
7. Asegure un buen acolchonamiento en cada superficie de contacto del robot, incluya accesorios como el control remoto, cargador, etc., y revise que no falte el revestimiento cuadrado en el centro del G1 antes de cerrar la caja.

---

## Solución de problemas

### 1. Falla en la autoprueba al encender

Si el robot está suspendido y, tras 2 minutos, no se escucha ningún sonido de las articulaciones de los tobillos, la inicialización ha fallado.  
**Solución:** Revise los pasos en "Revisión antes de encender" y "Preparación antes de encender". Intente encender el robot nuevamente.

### 2. Protección contra caídas

Si el robot pierde estabilidad y cae debido a la falta de fricción en el suelo o un manejo inadecuado, entrará en modo de autoprotección. El motor cambiará automáticamente a modo de frenado para proteger los componentes.

### 3. Problemas de conexión con la App

Si usa el modo de conexión directa AP, verifique que el teléfono esté conectado al hotspot del G1.  
Si la configuración falla: Asegúrese de que el nombre del hotspot no contenga símbolos ni espacios. Mantenga el robot y el teléfono cerca y reinicie tanto la app como el G1.

### 4. ¿Cómo apagar el robot si falla el control remoto?

Si el control remoto no funciona por batería agotada o un fallo técnico, apague el robot manualmente:  
- Suspenda el robot en un marco protector con ambas patas en el aire.  
- Asegure una distancia segura de 2 metros con cualquier obstáculo.  
- Presione brevemente el botón de encendido de la batería, luego manténgalo presionado por más de 2 segundos hasta que el G1 se apague.

### 5. El robot no se mantiene en pie tras encenderse

Si el G1 se cae fácilmente o no se estabiliza, intente reiniciarlo. Si el problema persiste, calibre las articulaciones en la Unitree Explore App.  
> **Nota:** No recalibre el robot a menos que sea estrictamente necesario. Si hay fallos, contacte primero a soporte técnico.  
Ruta en la app: **[Configuración] -> [Datos] -> [Robot] -> [Calibración]**.

### 6. Standby prolongado

Si necesita que el robot permanezca encendido por mucho tiempo, suspéndalo y póngalo en modo de amortiguación (**L1 + A**). Esto evitará que se apague automáticamente por baja batería y caiga repentinamente.

---

## Precauciones

### 1. Sobre la duración de la batería

- Autonomía aproximada: 2 horas en funcionamiento continuo.
- Factores que afectan la batería:
  - Velocidades altas prolongadas.
  - Ajustes constantes de postura.
  - Caminar con las piernas dobladas o con carga.
  - Desplazarse en terrenos irregulares o con pendientes.

### 2. Terrenos irregulares

Si el G1 camina en superficies con desniveles o pendientes, reduzca la velocidad y maneje con precaución para evitar que tropiece.

### 3. Velocidad máxima

Puede alcanzar hasta 2 m/s en terreno plano con configuración estable. Evite operar cerca de personas para prevenir accidentes. En condiciones normales, hay un límite de seguridad.

### 4. Seguridad en movimiento

- Mantenga el área despejada.
- Use cuerdas de tracción o un marco protector para evitar daños en el robot o lesiones a personas cercanas.
- No toque el robot mientras está en movimiento.

---

## Limpieza y almacenamiento

### Limpieza general

- Si el G1 tiene manchas tras su uso, límpielo de inmediato.
- Antes de limpiar:
  - Apague el robot.
  - Use un paño seco y suave para limpiar la superficie.
  - Limpie bien la cámara y el radar para evitar interferencias en su funcionamiento.

### Almacenamiento

- El G1 no es resistente al polvo ni al agua.
- Guárdelo en un lugar seco y fresco, lejos de la luz solar directa y la lluvia.
- Evite la humedad, ya que puede acortar la vida útil de las piezas debido a óxido o corrosión.

---

## Inspección y mantenimiento

Realizar revisiones rutinarias antes y después de su uso mejora el rendimiento, reduce riesgos de seguridad y extiende su vida útil.

### Inspección General del Robot

1. **Apariencia del Robot:**  
   - Verifique que el cuerpo esté limpio, sin daños ni marcas de deformación.  
   - Revise la lente de la cámara para asegurarse de que no haya suciedad o partículas extrañas.  
   - Compruebe el LiDAR montado en la cabeza para asegurarse de que no haya obstrucciones a su alrededor.

2. **Estructura del Robot:**  
   - Realice revisión visual y táctil: cuerpo, articulaciones, conexiones y extremidades deben estar en buen estado.  
   - Si hay grietas o daños visibles, reemplácelos de inmediato y contacte con el servicio técnico de Unitree Robotics.  
   - Verifique los tornillos de las conexiones, especialmente los de las articulaciones y los pomos de bloqueo de la batería.  
   - Revise las entradas y salidas del ventilador de enfriamiento para asegurarse de que no haya obstrucciones.

3. **Revisión de las Piezas de los Pies:**  
   - Verifique si hay daños evidentes en las almohadillas de los pies. Si están dañadas, reemplácelas lo antes posible.

4. **Revisión de los Paquetes de Batería:**  
   - Compruebe el puerto de la batería: asegúrese de que no haya suciedad o deformaciones en el conector.  
   - Verifique que la batería esté bien instalada para evitar que se suelte durante la operación.  
   - Inspeccione la carcasa de la batería: si tiene daños visibles, no la use.

5. **Revisión del Control Remoto:**  
   - Inspeccione la palanca de control: asegúrese de que esté en posición central y que no tenga arena u otros residuos.  
   - Revise que todas las teclas funcionen correctamente, sin retrasos o atascos.  
   - Confirme que la batería del control remoto tenga suficiente carga.

6. **Revisión del Ventilador de Enfriamiento:**  
   - Escuche atentamente para asegurarse de que el ventilador funciona correctamente y no emite ruidos anormales, como raspaduras.

### Mantenimiento del Paquete de Batería

1. Nunca cargue la batería en un entorno con temperaturas extremas.
2. No almacene la batería en lugares con temperaturas superiores a 40°C.
3. Evite la sobrecarga de la batería para prevenir daños en las celdas.
4. Si la batería no se usará por mucho tiempo:
   - Verifique periódicamente la carga.
   - Si la carga está por debajo del 30%, cárguela hasta el 70% antes de guardarla para evitar una descarga excesiva y daños en la batería.

> **Recomendación:** Realice esta inspección antes de cada uso. Si alguna pieza está dañada y necesita ser reemplazada, contacte con el servicio postventa de Unitree Robotics.

---

## Historial de versiones

| Versión | Fecha      | Descripción |
|---------|------------|-------------|
| 1.1     | 28/10/2024 | Nombre de las piezas - Añadir la posición del orificio de instalación CI |
| 1.0     | 03/09/2024 | Versión inicial |