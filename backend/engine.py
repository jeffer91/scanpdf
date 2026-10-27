"""ScanPDF engine: read-only inspection of PDFs, offline OCR, signatures and XLSX.

Protocol: JSON-lines on stdout for scan, JSON from stdin for export.
The original PDF bytes are never modified or OCR-stamped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

MAX_OCR_PAGES = 20  # sensible local default; report partial OCR explicitly
VERSION = 1


def publish(**data):
    print(json.dumps(data, ensure_ascii=False, default=str), flush=True)


def normalize(value: str) -> str:
    s = unicodedata.normalize('NFKD', str(value or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', re.sub(r'[^A-Za-z0-9 ]', ' ', s)).upper().strip()


def parse_filename(name: str) -> dict:
    stem = Path(name).stem
    # Remove auto-numbering created by Explorer without corrupting the name.
    stem = re.sub(r'\s*\(\d+\)$', '', stem).strip()
    # Example: UGPA-RGI1-01-PRO-251-2026-07-Danny Patricio Bermeo Ochoa
    matched = re.match(r'^(.+?-\d+)-(PRO-\d+)-(20\d{2}-\d{2})-(.+)$', stem, re.I)
    if matched:
        return {'codigo': matched.group(1), 'proceso': matched.group(2),
                'periodo': matched.group(3), 'docente': matched.group(4).strip()}
    return {'codigo': '', 'proceso': '', 'periodo': '', 'docente': stem.replace('_', ' ').strip()}


DATE_RE = re.compile(r'(?<!\d)(\d{1,2})[/-](\d{1,2})[/-]((?:19|20)\d{2})(?!\d)')
DATE_TEXT_RE = re.compile(r'(?i)(?:fecha(?: de (?:elaboraci[oó]n|emisi[oó]n))?|quito\s*,?\s*a)\s*:?\s*([^\n]{0,75})')
MONTHS = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6,
          'julio': 7, 'agosto': 8, 'septiembre': 9, 'setiembre': 9, 'octubre': 10,
          'noviembre': 11, 'diciembre': 12}
TEXT_DATE_RE = re.compile(r'(?i)(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\s+de\s+((?:19|20)\d{2})')


def fmt_date(day: int, month: int, year: int) -> str:
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return ''


def extract_date(text: str) -> str:
    # Prefer dates explicitly labelled as date. Never use file mtime as substitute.
    candidates = [m.group(1) for m in DATE_TEXT_RE.finditer(text)]
    candidates.append(text[:5000])
    for candidate in candidates:
        found = DATE_RE.search(candidate)
        if found:
            result = fmt_date(*map(int, found.groups()))
            if result: return result
        found_text = TEXT_DATE_RE.search(candidate)
        if found_text:
            day, month, year = found_text.groups()
            result = fmt_date(int(day), MONTHS[month.lower()], int(year))
            if result: return result
    return ''


def detect_document_teacher(text: str) -> str:
    # Conservative: if no explicit label is found, rely on filename and mark unconfirmed.
    patterns = [
        r'(?im)^\s*(?:nombre\s+(?:del|de la)\s+)?docente\s*:\s*([^\n\r]{6,100})',
        r'(?im)^\s*nombre\s+(?:completo|y\s+apellido)\s*:\s*([^\n\r]{6,100})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = re.split(r'\s{3,}|\||\t', match.group(1))[0].strip(' :-')
            if 2 <= len(candidate.split()) <= 8:
                return candidate
    return ''


def teacher_match(name: str, text: str) -> str:
    words = [w for w in normalize(name).split() if len(w) > 2]
    if len(words) < 2 or not text.strip(): return 'No comprobable'
    corpus = normalize(text)
    matches = sum(1 for w in words if re.search(r'\b' + re.escape(w) + r'\b', corpus))
    return 'Coincide' if matches >= max(2, int(len(words) * 0.75 + 0.5)) else 'No localizado'


_OCR = None

def ocr_page(page) -> str:
    global _OCR
    if _OCR is None:
        from rapidocr import RapidOCR
        _OCR = RapidOCR()
    # Render in memory to avoid touching existing PDF and modifying signatures.
    pix = page.get_pixmap(matrix=__import__('fitz').Matrix(1.65, 1.65), alpha=False)
    result = _OCR(pix.tobytes('png'))
    return '\n'.join(str(x) for x in (result.txts or []))


def inspect_text(pdf_path: Path) -> dict:
    import fitz
    text_blocks, ocr_pages, digital_pages = [], 0, 0
    with fitz.open(str(pdf_path)) as doc:
        if doc.needs_pass:
            raise ValueError('PDF protegido con contraseña')
        pages = len(doc)
        missing_ocr_pages = 0
        for i in range(pages):
            page = doc[i]
            extracted = page.get_text('text', sort=True) or ''
            # Scanned PDFs often have 0-25 printable characters per page.
            if len(re.sub(r'\s', '', extracted)) >= 45:
                digital_pages += 1
            else:
                if ocr_pages < MAX_OCR_PAGES:
                    try:
                        recognized = ocr_page(page)
                        if recognized: extracted = extracted + '\n' + recognized
                        ocr_pages += 1
                    except Exception as exc:
                        raise RuntimeError(f'El OCR no pudo procesar la página {i + 1}: {exc}') from exc
                else:
                    missing_ocr_pages += 1
            text_blocks.append(extracted)
    return {'texto': '\n'.join(text_blocks), 'paginas': pages, 'paginas_ocr': ocr_pages, 'paginas_digitales': digital_pages,
            'paginas_ocr_omitidas': missing_ocr_pages}


def sign_name(cert) -> str:
    try:
        native = cert.subject.native
        val = native.get('common_name') or native.get('given_name') or native.get('organization_name') or ''
        return str(val)
    except Exception:
        return ''


def check_signatures(pdf_path: Path) -> dict:
    """Offline validation: never conflate cryptographic integrity with certificate trust.

    Revocation checks may be unavailable without network; status stays inconclusive.
    """
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    findings = []
    with open(pdf_path, 'rb') as stream:
        reader = PdfFileReader(stream)
        embedded = list(reader.embedded_signatures)
        for index, sig in enumerate(embedded, 1):
            item = {'numero': index, 'firmante': '', 'fecha_reportada': '',
                    'sello_tiempo': '', 'estado': 'Indeterminada', 'integridad': 'Sin evaluar',
                    'confianza': 'No comprobada', 'detalle': '', 'campo': str(getattr(sig, 'field_name', '') or '')}
            try:
                item['firmante'] = sign_name(sig.signer_cert)
            except Exception:
                pass
            try:
                status = validate_pdf_signature(sig, ValidationContext(allow_fetching=False))
                item['firmante'] = sign_name(status.signing_cert) or item['firmante']
                item['integridad'] = 'Íntegra' if (status.intact and status.valid) else 'Alterada'
                item['confianza'] = 'Verificada' if status.trusted else 'No comprobada'
                dt_reported = getattr(status, 'signer_reported_dt', None)
                if dt_reported: item['fecha_reportada'] = dt_reported.isoformat(timespec='seconds')
                timestamp = getattr(status, 'timestamp_validity', None)
                if timestamp:
                    item['sello_tiempo'] = 'Confiable' if timestamp.trusted and timestamp.valid and timestamp.intact else 'No verificado'
                if not status.valid or not status.intact or status.docmdp_ok is False:
                    item['estado'] = 'Inválida'
                    item['detalle'] = 'No pasó la comprobación criptográfica o presenta modificaciones incompatibles.'
                elif status.bottom_line:
                    item['estado'] = 'Válida'
                    item['detalle'] = 'Validada con la configuración de confianza disponible (sin consultar revocación en línea).'
                else:
                    item['estado'] = 'Indeterminada'
                    item['detalle'] = 'Integridad correcta, pero no pudo establecerse toda la confianza del certificado sin comprobación adicional.'
            except Exception as exc:
                item['estado'] = 'Indeterminada'
                item['detalle'] = f'La firma está presente, pero su validación falló: {type(exc).__name__}: {str(exc)[:150]}'
            findings.append(item)
    return {'firmas': findings, 'firmas_cantidad': len(findings)}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_file(path: Path) -> dict:
    info = parse_filename(path.name)
    row = {'archivo': path.name, 'ruta': str(path), **info,
           'docente_documento': '', 'nombre_coincide': 'No comprobable',
           'fecha_documento': '', 'fecha_modificacion': dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec='seconds'),
           'tipo_pdf': 'Sin determinar', 'paginas': 0, 'paginas_digitales': 0, 'paginas_ocr': 0,
           'paginas_ocr_omitidas': 0, 'firma': 'Sin comprobar', 'firmas': [],
           'firmas_cantidad': 0, 'estado': 'Revisar', 'observaciones': [],
           'sha256': '', 'duplicado_de': '', 'error': ''}
    try:
        row['sha256'] = file_hash(path)
        content = inspect_text(path)
        text = content.pop('texto')
        row.update(content)
        row['tipo_pdf'] = ('Escaneado / OCR' if content['paginas_ocr'] and not content['paginas_digitales']
                           else 'Mixto / OCR' if content['paginas_ocr'] else 'Digital')
        row['docente_documento'] = detect_document_teacher(text)
        row['nombre_coincide'] = teacher_match(info['docente'], text)
        row['fecha_documento'] = extract_date(text)
        if not row['fecha_documento']:
            row['observaciones'].append('Fecha de elaboración no identificada en el contenido')
        if row['nombre_coincide'] != 'Coincide':
            row['observaciones'].append('Nombre del docente no confirmado dentro del PDF')
        if content['paginas_ocr_omitidas']:
            row['observaciones'].append(f'OCR parcial: {content["paginas_ocr_omitidas"]} páginas sin OCR (límite de {MAX_OCR_PAGES})')
        try:
            checked = check_signatures(path)
            row.update(checked)
        except Exception as exc:
            row['firma'] = 'No verificable'
            row['observaciones'].append(f'No fue posible inspeccionar firmas: {type(exc).__name__}: {str(exc)[:120]}')
        if row['firma'] != 'No verificable':
            if not row['firmas']:
                visual = bool(re.search(r'(?i)firmado\s+electr[oó]nicamente|firma\s+electr[oó]nica|firma\s+digital|firmado\s+digitalmente', text))
                row['firma'] = 'Texto de firma visible' if visual else 'Sin firma digital'
                row['observaciones'].append('Existe texto alusivo a firma, pero no firma digital verificable' if visual else 'No se encontró firma digital incorporada al PDF')
            elif any(s['estado'] == 'Inválida' for s in row['firmas']):
                row['firma'] = 'Inválida'
                row['observaciones'].append('Al menos una firma digital no superó la validación criptográfica')
            elif any(s['estado'] != 'Válida' for s in row['firmas']):
                row['firma'] = 'Por confirmar'
                row['observaciones'].append('La confianza de una o más firmas requiere comprobación adicional')
            else:
                row['firma'] = 'Válida'
        # A valid signature does not imply the signer matches the docente. Report separately.
        if row['firmas'] and info['docente']:
            name_norm = normalize(info['docente'])
            signer_matches = any(normalize(f['firmante']) == name_norm or
                                 (len(set(normalize(f['firmante']).split()) & set(name_norm.split())) >= 3)
                                 for f in row['firmas'] if f['firmante'])
            if not signer_matches:
                row['observaciones'].append('Ningún firmante identificado coincide claramente con el nombre del docente (podría ser una autoridad)')
        row['estado'] = ('Correcto' if row['firma'] == 'Válida' and row['nombre_coincide'] == 'Coincide'
                         and row['fecha_documento'] and not row['observaciones'] else
                         'Incidencia' if row['firma'] == 'Inválida' else 'Revisar')
    except Exception as exc:
        row['error'] = f'{type(exc).__name__}: {str(exc)[:260]}'
        row['firma'] = 'No evaluada'
        row['estado'] = 'Error'
        row['observaciones'].append('No fue posible leer el documento. ' + row['error'])
    return row


def load_cache(cache_path: Path) -> dict:
    try:
        cached = json.loads(cache_path.read_text(encoding='utf-8'))
        return cached.get('files', {}) if cached.get('version') == VERSION else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_cache(cache_path: Path, cache: dict):
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=cache_path.parent, delete=False, suffix='.tmp') as stream:
        json.dump({'version': VERSION, 'files': cache}, stream, ensure_ascii=False)
        tmp = stream.name
    os.replace(tmp, cache_path)


def scan(folder: Path, cache_path: Path):
    if not folder.is_dir():
        publish(event='fatal', text='La carpeta seleccionada no existe.')
        return 2
    pdfs = sorted((p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() == '.pdf'), key=lambda x: str(x).casefold())
    if not pdfs:
        publish(event='start', total=0)
        publish(event='complete', count=0)
        return 0
    cache = load_cache(cache_path)
    publish(event='start', total=len(pdfs))
    seen_hashes = {}
    for index, file in enumerate(pdfs, 1):
        try:
            stat = file.stat()
            key = str(file.resolve())
            entry = cache.get(key, {})
            fingerprint = [stat.st_size, stat.st_mtime_ns]
            if entry.get('fingerprint') == fingerprint and isinstance(entry.get('row'), dict):
                row = dict(entry['row'])
                row['observaciones'] = list(row.get('observaciones', []))
                cached = True
            else:
                row = inspect_file(file)
                cache[key] = {'fingerprint': fingerprint, 'row': row}
                cached = False
            if row.get('sha256'):
                if row['sha256'] in seen_hashes:
                    row['duplicado_de'] = seen_hashes[row['sha256']]
                    row['observaciones'].append('Duplicado exacto de otro PDF de la carpeta')
                    if row['estado'] == 'Correcto': row['estado'] = 'Revisar'
                else:
                    seen_hashes[row['sha256']] = row['ruta']
            publish(event='item', index=index, total=len(pdfs), cached=cached, row=row)
        except Exception as exc:
            publish(event='warning', text=f'No se pudo procesar {file.name}: {exc}')
        if index % 5 == 0: save_cache(cache_path, cache)
    save_cache(cache_path, cache)
    publish(event='complete', count=len(pdfs))
    return 0


HEADERS = [
    ('archivo', 'Archivo'), ('codigo', 'Código'), ('proceso', 'Proceso'), ('periodo', 'Período'),
    ('docente', 'Docente (archivo)'), ('docente_documento', 'Docente (contenido)'),
    ('nombre_coincide', 'Coincidencia nombre'), ('fecha_documento', 'Fecha documento'),
    ('fecha_modificacion', 'Fecha modificación archivo'), ('tipo_pdf', 'Tipo PDF'),
    ('paginas', 'Páginas'), ('paginas_ocr', 'Páginas OCR'), ('firma', 'Firma digital'),
    ('firmas_cantidad', 'Total firmas'), ('estado', 'Resultado'),
    ('observaciones', 'Observaciones'), ('duplicado_de', 'Duplicado de'), ('ruta', 'Ruta original')]


def export_xlsx(output: Path, rows: list, folder: str = ''):
    import xlsxwriter
    workbook = xlsxwriter.Workbook(str(output))
    title_fmt = workbook.add_format({'bold': True, 'font_size': 19, 'font_color': '#15283E'})
    subtitle_fmt = workbook.add_format({'font_color': '#718198', 'font_size': 10})
    head_fmt = workbook.add_format({'bold': True, 'bg_color': '#e8eef8', 'font_color': '#253d59', 'bottom': 1,
                                    'bottom_color': '#cad7e6', 'text_wrap': True, 'valign': 'vcenter'})
    line_fmt = workbook.add_format({'valign': 'top', 'font_color': '#263448', 'text_wrap': True})
    ok_fmt = workbook.add_format({'valign': 'top', 'bg_color': '#eaf6ef', 'font_color': '#17653b'})
    warn_fmt = workbook.add_format({'valign': 'top', 'bg_color': '#fff5e5', 'font_color': '#8b5a10'})
    bad_fmt = workbook.add_format({'valign': 'top', 'bg_color': '#ffeded', 'font_color': '#962d2d'})

    def write(sheet, row_i, col_i, value, fmt=line_fmt):
        if isinstance(value, list): value = ' | '.join(str(v) for v in value)
        elif value is None: value = ''
        # Explicitly write strings to prevent spreadsheet formula injection.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            sheet.write_number(row_i, col_i, value, fmt)
        else:
            sheet.write_string(row_i, col_i, str(value)[:32000], fmt)

    def start_sheet(name, headers):
        sh = workbook.add_worksheet(name)
        sh.hide_gridlines(2)
        sh.freeze_panes(4, 0)
        sh.set_landscape()
        sh.merge_range(0, 0, 0, len(headers)-1, 'ScanPDF · Auditoría documental', title_fmt)
        sh.merge_range(1, 0, 1, len(headers)-1,
                       f'Carpeta: {folder}   |   Generado: {dt.datetime.now():%d/%m/%Y %H:%M}', subtitle_fmt)
        sh.set_row(3, 34)
        for col, header in enumerate(headers): write(sh, 3, col, header, head_fmt)
        return sh

    docs = start_sheet('Documentos', [label for _, label in HEADERS])
    widths = [42, 20, 13, 13, 35, 35, 20, 17, 23, 18, 11, 14, 22, 13, 14, 65, 43, 75]
    for i, width in enumerate(widths): docs.set_column(i, i, width)
    for i, row in enumerate(rows, 4):
        for j, (key, _) in enumerate(HEADERS):
            value = row.get(key, '')
            fmt = (ok_fmt if value == 'Correcto' else bad_fmt if value in ('Error', 'Incidencia', 'Inválida')
                   else warn_fmt if value == 'Revisar' else line_fmt)
            write(docs, i, j, value, fmt)
    if rows: docs.autofilter(3, 0, 3 + len(rows), len(HEADERS)-1)
    sig_headers = ['Archivo', 'Docente', 'Nº firma', 'Firmante certificado', 'Fecha declarada',
                   'Sello de tiempo', 'Integridad', 'Confianza', 'Estado', 'Campo de firma', 'Detalle', 'Ruta']
    sigs = start_sheet('Firmas', sig_headers)
    for j, width in enumerate([40, 33, 12, 36, 24, 20, 15, 20, 20, 20, 67, 75]): sigs.set_column(j, j, width)
    sidx = 4
    for row in rows:
        for sign in row.get('firmas', []):
            vals = [row.get('archivo', ''), row.get('docente', ''), sign.get('numero', ''), sign.get('firmante', ''),
                    sign.get('fecha_reportada', ''), sign.get('sello_tiempo', ''), sign.get('integridad', ''),
                    sign.get('confianza', ''), sign.get('estado', ''), sign.get('campo', ''),
                    sign.get('detalle', ''), row.get('ruta', '')]
            for col, val in enumerate(vals): write(sigs, sidx, col, val)
            sidx += 1
    if sidx > 4: sigs.autofilter(3, 0, sidx-1, len(sig_headers)-1)
    issues = [r for r in rows if r.get('estado') != 'Correcto']
    issues_headers = ['Archivo', 'Docente', 'Resultado', 'Firma', 'Observaciones', 'Ruta original']
    inc = start_sheet('Incidencias', issues_headers)
    for j, width in enumerate([44, 38, 19, 23, 75, 75]): inc.set_column(j, j, width)
    for index, row in enumerate(issues, 4):
        for j, val in enumerate([row.get('archivo', ''), row.get('docente', ''), row.get('estado', ''),
                                  row.get('firma', ''), row.get('observaciones', []), row.get('ruta', '')]):
            write(inc, index, j, val, bad_fmt if j == 2 and row.get('estado') in ('Error','Incidencia') else line_fmt)
    if issues: inc.autofilter(3, 0, 3 + len(issues), 5)
    workbook.close()


def main():
    parser = argparse.ArgumentParser(description='ScanPDF engine')
    subs = parser.add_subparsers(dest='mode', required=True)
    sc = subs.add_parser('scan')
    sc.add_argument('--folder', required=True)
    sc.add_argument('--cache', required=True)
    ex = subs.add_parser('export')
    ex.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.mode == 'scan': return scan(Path(args.folder), Path(args.cache))
    raw = json.load(sys.stdin)
    if not isinstance(raw.get('rows'), list): raise ValueError('Se requiere una lista de documentos')
    export_xlsx(Path(args.output), raw['rows'], raw.get('folder') or '')
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
