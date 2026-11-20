# Firma Authenticode gratuita con SignPath Foundation

Guía para pedir la firma gratuita de [SignPath Foundation](https://signpath.org/) para proyectos de código abierto y
firmar el instalador (y el exe) desde GitHub Actions. Es independiente de la firma Ed25519 de `SHA256SUMS.txt` que
usa el actualizador (ver `docs/PACKAGING.md`): esa protege las actualizaciones automáticas; Authenticode es la firma
que Windows (SmartScreen, Smart App Control) reconoce.

> **Aviso.** Esta guía está escrita de memoria y **no se ha verificado** contra la web actual de SignPath. Todo lo
> marcado con **[CONFIRMAR]** debe comprobarse en la documentación oficial antes de actuar: requisitos, nombres de
> campos, versiones de las acciones y límites pueden haber cambiado. No se incluye ningún secreto, token ni
> identificador real.

## 1. Requisitos

Comprueba en [signpath.org](https://signpath.org/) (sección de la fundación / "Open Source" y su política de
elegibilidad) **[CONFIRMAR]** que el proyecto cumple. Lo habitual para esta clase de programas:

- Repositorio público con licencia de código abierto aprobada. Este repo usa MIT (`LICENSE`).
- Proyecto activo, con releases públicas y sin software malicioso, adware ni comportamiento que los antivirus
  puedan marcar. **[CONFIRMAR]** si hay una lista de comportamientos prohibidos: el widget carga un driver
  (PawnIO) y se ejecuta elevado, así que conviene explicarlo bien en la solicitud.
- Los binarios firmados deben construirse a partir del código fuente público, de forma reproducible y sin pasos
  manuales opacos. SignPath suele exigir que la compilación se haga en un sistema de CI verificable (GitHub
  Actions con runners alojados por GitHub) **[CONFIRMAR]**.
- Políticas de privacidad y de firma/código de conducta visibles en el repo o en la web (normalmente se pide una
  página que diga quién puede aprobar firmas y qué se firma) **[CONFIRMAR]**.
- Identidad del solicitante y del equipo que aprueba las firmas (autenticación en dos pasos en GitHub y en SignPath)
  **[CONFIRMAR]**.
- Atribución: poner en el README y en la página de releases el texto y el enlace que SignPath pide
  (por ejemplo "Free code signing provided by SignPath.io, certificate by SignPath Foundation") **[CONFIRMAR el
  texto exacto]**.

## 2. Preparar el repositorio antes de pedirlo

1. Añadir al README una sección breve "Code signing" con la atribución exigida y una política: quién aprueba las
   firmas, qué archivos se firman y que solo se firman releases construidas en GitHub Actions.
2. Tener un workflow de release reproducible que construya `HardwareMonitorWidget.exe` e
   `HardwareMonitorWidget-Setup-X.Y.Z.exe` en un runner de GitHub (hoy se compila en local con
   `packaging\build.ps1`; habrá que portarlo a Actions: Python 3.14, .NET 8 SDK, Inno Setup 6).
3. Decidir qué se firma. Propuesta: el instalador `Setup` y, si el flujo lo permite, `HardwareMonitorWidget.exe` y
   `SensorHost.exe` antes de empaquetarlos. Firmar los exe internos requiere un paso previo de firma antes de
   ejecutar Inno Setup **[CONFIRMAR]** cómo encadenar dos firmas en un mismo workflow.
4. Mantener el repo y la cuenta de GitHub con 2FA.

## 3. Solicitar el acceso

1. Entrar en la web de SignPath Foundation y abrir el formulario de solicitud para proyectos open source
   **[CONFIRMAR URL y proceso actuales]**.
2. Rellenar: nombre del proyecto, URL del repo, licencia, descripción, contacto y la persona que actuará como
   aprobador. Explicar qué hace el programa, por qué necesita elevación y el driver PawnIO.
3. Esperar la revisión. El plazo y los criterios son de SignPath **[CONFIRMAR]**. No se garantiza la aprobación.
4. Si se aprueba, SignPath crea una **organización** y un **proyecto** en su plataforma, con un certificado emitido
   a nombre de la fundación **[CONFIRMAR]**. El nombre del editor que verá Windows será el del certificado, no el
   de este repo.

## 4. Configurar SignPath (panel web)

Los nombres exactos pueden variar **[CONFIRMAR]**; el esquema general es:

1. **Project**: slug del proyecto (por ejemplo `hardware-monitor-widget`).
2. **Artifact configuration**: define qué se firma dentro del artefacto que sube el workflow (un ZIP con el
   `Setup.exe`, por ejemplo) y con qué método (Authenticode para `.exe`). Hay una plantilla XML para exe/msi.
3. **Signing policy**: p. ej. `release-signing` (con aprobación manual) y quizá `test-signing` para pruebas.
   Las releases públicas deben usar la política con el certificado real.
4. **Trusted build system**: vincular GitHub como sistema de compilación de confianza, de modo que SignPath
   compruebe que el artefacto procede de ese repo y workflow (origen verificable) **[CONFIRMAR]**.
5. **CI user y token API**: crear un usuario de CI y un token. El token se guarda **solo** como secreto de GitHub
   (nunca en el repo).
6. Anotar el **Organization ID** (no es secreto, pero tampoco se inventa: se copia del panel).

## 5. Integración con GitHub Actions

Secretos y variables del repositorio (Settings > Secrets and variables > Actions):

| Nombre | Tipo | Contenido |
| --- | --- | --- |
| `SIGNPATH_API_TOKEN` | Secret | Token del usuario de CI de SignPath |
| `SIGNPATH_ORGANIZATION_ID` | Variable | ID de la organización (del panel) |

Esquema de workflow de release (borrador: **no está en el repo** y los parámetros de la acción deben contrastarse
con su documentación **[CONFIRMAR]**):

```yaml
name: Release
on:
  push:
    tags: ["v*"]

permissions:
  contents: read

jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      # ... instalar Python 3.14, .NET 8, Inno Setup y ejecutar packaging\build.ps1 ...
      - id: upload
        uses: actions/upload-artifact@v4
        with:
          name: unsigned-setup
          path: dist/HardwareMonitorWidget-Setup-*.exe

  sign:
    needs: build
    runs-on: windows-latest
    permissions:
      contents: read
      actions: read          # SignPath lee los metadatos del run  [CONFIRMAR]
    steps:
      - uses: signpath/github-action-submit-signing-request@v1   # [CONFIRMAR versión y parámetros]
        with:
          api-token: ${{ secrets.SIGNPATH_API_TOKEN }}
          organization-id: ${{ vars.SIGNPATH_ORGANIZATION_ID }}
          project-slug: hardware-monitor-widget          # [CONFIRMAR slug real]
          signing-policy-slug: release-signing           # [CONFIRMAR slug real]
          github-artifact-id: ${{ needs.build.outputs.artifact-id }}   # [CONFIRMAR cómo se obtiene]
          wait-for-completion: true
          output-artifact-directory: signed
      # Después: regenerar SHA256SUMS.txt con el instalador FIRMADO, firmar SHA256SUMS.txt con Ed25519
      # (tools\signing\sign_release.py) y subir todo a la release.
```

Puntos a tener en cuenta:

- **El hash cambia al firmar.** `SHA256SUMS.txt`, su firma `.sig` (Ed25519) y el hash del manifiesto winget deben
  calcularse sobre el instalador ya firmado. Hoy el flujo calcula el hash al construir; habrá que reordenarlo.
- La política de firma de release suele requerir **aprobación manual** en SignPath; el job espera hasta que alguien
  apruebe **[CONFIRMAR]**.
- No usar `pull_request` desde forks para firmar: los secretos no deben exponerse a código no revisado.
- Fijar las acciones de terceros por versión (o por SHA) una vez confirmadas.

## 6. Verificación tras la primera firma

1. Descargar el instalador firmado y comprobar: `Get-AuthenticodeSignature .\HardwareMonitorWidget-Setup-X.Y.Z.exe`
   (Status `Valid`, sujeto igual al certificado de la fundación).
2. Propiedades del archivo > Firmas digitales.
3. Probar la instalación en un equipo con SmartScreen y con Smart App Control activos. Una firma nueva puede seguir
   mostrando avisos de reputación durante un tiempo **[CONFIRMAR si el certificado de SignPath Foundation hereda
   reputación]**.
4. Con firma válida se puede reconsiderar el mensaje `upd_err_blocked` y el texto de `docs/SECURITY_NOTES.md`.
5. Actualizar los manifiestos de `packaging/winget/` con la URL y el SHA-256 del instalador firmado.

## 7. Lo que NO sabemos y hay que confirmar

- Plazos, criterios y probabilidad de aprobación de SignPath Foundation.
- Texto exacto de atribución y dónde debe ir.
- Nombre y versión de la acción de GitHub, parámetros y cómo se pasa el artefacto.
- Si se pueden firmar varios binarios en una misma ejecución o hacen falta dos solicitudes.
- Si el driver PawnIO o la ejecución elevada plantean objeciones en la revisión.
- Límites de uso (número de firmas, tamaño de archivo).
- Cómo afecta el cambio de editor mostrado (nombre de la fundación) a SmartScreen y a winget.
