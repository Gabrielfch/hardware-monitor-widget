# Changelog

Todos los cambios relevantes de este proyecto se documentan en este archivo.
El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto usa
[versionado semántico](https://semver.org/lang/es/).

## [Unreleased]

## [0.1.0] - 2026-11-18

Primera versión pública.

### Added
- Lectura nativa de sensores con un motor propio (`SensorHost`, .NET 8 autocontenido, LibreHardwareMonitorLib
  0.9.6 y el controlador PawnIO). No depende de otras aplicaciones de monitorización.
- Píldoras en la barra de tareas junto al área de notificación, agrupadas por componente: un icono por hardware
  y un valor por sensor fijado, cada uno con su color de banda y su barra. El borde se pone rojo cuando un valor
  es crítico, el desbordamiento oculta componentes enteros con una píldora «+N», y se admite la barra vertical.
  La opción «Agrupar píldoras por componente» permite volver a una píldora por sensor.
- Convivencia con Taskbar Battery Widget: las píldoras se colocan sin solaparse.
- Panel con tarjetas por componente (CPU, GPU, RAM, memoria virtual, cada disco, red y placa base) y un resumen
  curado: temperaturas, cargas, ventiladores, frecuencias, potencia, memoria y lectura/escritura de disco.
  Mín/máx al pasar el ratón o con el foco.
- Ventana de detalle con todos los sensores, búsqueda, y casillas «En la barra» y «Resumen».
- Fijar o quitar de una vez todos los sensores del resumen de un componente (hasta 32 fijados).
- Ajustes en el propio panel:
  - Personalización: tema claro/oscuro/sistema, paletas, colores e iconos personalizados, escala e idioma
    (español/inglés).
  - Visualización: qué hardware y qué categorías se muestran.
  - Posición: barra derecha/izquierda, monitor, flotante, pantalla completa.
  - Inicio con Windows, registros con nivel debug, comprobación de actualizaciones y Acerca de.
- Clic derecho en las píldoras y en la bandeja con el mismo menú que Taskbar Battery Widget.
- Instalador (Program Files, siempre elevado) y versión portable.
- Actualizador con `SHA256SUMS.txt` firmado con Ed25519 (se activa solo si la compilación embebe claves públicas).

### Security
- La app y el motor corren elevados (UAC). El inicio con Windows mediante tarea programada solo se crea si la
  carpeta de instalación es escribible únicamente por administradores; en otro caso se usa la clave Run de HKCU.
- Las carpetas de iconos, actualizaciones, configuración y registros se protegen frente a junctions o enlaces
  simbólicos, propietarios no confiables y permisos de escritura de usuarios estándar.
- Detalles en `docs/SECURITY_NOTES.md`.

[Unreleased]: https://github.com/Gabrielfch/hardware-monitor-widget/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Gabrielfch/hardware-monitor-widget/releases/tag/v0.1.0
