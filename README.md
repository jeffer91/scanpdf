# ScanPDF · Auditoría local de PDF

Aplicación de escritorio **Electron** para analizar carpetas enteras de PDF docentes (digitales, escaneados y mixtos). Lee el nombre desde el archivo, extrae texto, ejecuta OCR local cuando se necesita, inspecciona firmas digitales incrustadas y exporta un Excel con **Documentos**, **Firmas** e **Incidencias**.

## Instalar en Windows (sin programar)

1. Abre **Actions** en este repositorio.
2. Entra en la ejecución más reciente de **Windows · instalador** que haya terminado correctamente.
3. Abajo, en **Artifacts**, descarga **ScanPDF-Windows** y extrae el ZIP.
4. Ejecuta el instalador `ScanPDF Setup ...exe`.
5. Abre ScanPDF, selecciona la carpeta y pulsa **Analizar carpeta**. Al terminar, pulsa **Exportar Excel**.

El instalador es generado por GitHub Actions sobre Windows, e incluye el motor Python y sus dependencias. La primera ejecución del OCR puede tardar más mientras el motor carga los modelos locales. El instalador no está firmado con un certificado comercial de editor; Windows SmartScreen puede mostrar una advertencia.

## Desarrollo

Requiere Node.js 22+ y Python 3.12+:

```bash
python -m pip install -r backend/requirements.txt
npm install
npm start
```

Para comprobar el motor sin abrir la ventana:

```bash
python backend/engine.py scan --folder "C:\\Documentos\\Docentes" --cache "cache.json"
```

## Qué comprueba

- PDF digitales: extrae texto con PyMuPDF.
- PDF escaneados: OCR local RapidOCR (máximo 20 páginas que requieran OCR por archivo; si hay más, se informa en las observaciones).
- Nombres: interpreta archivos como `UGPA-RGI1-01-PRO-251-2026-07-Nombre Apellido.pdf` y trata de localizar el docente dentro del contenido.
- Fechas: identifica fechas textuales en el PDF. **No utiliza** la fecha de modificación del archivo como prueba de fecha de elaboración.
- Firmas digitales: pyHanko inspecciona todas las firmas incrustadas y separa integridad criptográfica de confianza del certificado. Si no puede comprobar toda la cadena de confianza o la revocación fuera de línea, marca el resultado **Por confirmar**, no «Válida».
- Firmas visibles o textos «firmado electrónicamente» que no tengan firma PDF incrustada se clasifican como **no verificables criptográficamente**.
- Duplicados exactos: detecta por SHA-256.
- Caché: reutiliza archivos no modificados por ruta, tamaño y fecha de modificación.

**Importante:** La verificación en modo local no equivale a una validación jurídica/oficial completa de certificados ecuatorianos. En particular, puede ser necesario verificar revocación, autoridades de confianza y sello de tiempo por un mecanismo oficial como FirmaEC. Tampoco se puede identificar una firma manuscrita solo por la presencia de una imagen de rúbrica.

## Privacidad

Los PDF originales se abren **solo en modo lectura**, y la aplicación no envía los documentos a servicios externos. Guarda el historial y la caché en la carpeta local de datos de ScanPDF. Los modelos OCR se descargan en **la compilación del instalador**, no durante el análisis normal.

## Limitaciones conocidas

- Los nombres y fechas extraídos por OCR pueden requerir revisión humana.
- Los PDF protegidos por contraseña o dañados se reportan como error.
- Las fechas declaradas dentro de las firmas no son necesariamente sellos de tiempo confiables.
- Para volver a analizar un PDF modificado, conserva la ruta y selecciona nuevamente la carpeta; la caché comprobará tamaño y fecha.
- La coincidencia entre nombre del docente y firmante no exige que sean iguales: una autoridad institucional puede ser el firmante correcto, y se marca como observación para revisión.
- Se genera instalador **Windows x64**; no se publica una versión macOS/Linux en este flujo.

## Estructura

```
main.js                  Ventana y operaciones seguras de Electron
preload.js               API limitada y aislada para la pantalla
renderer/index.html      Pantalla única
renderer/styles.css      Estilos minimalistas
renderer/app.js          Interacción y resultados
backend/engine.py        PDF, OCR, firmas, caché y Excel
backend/requirements.txt Dependencias Python
.github/workflows/       Instalador automático Windows
```
