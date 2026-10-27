import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import engine


def test_institutional_name():
    v = engine.parse_filename('UGPA-RGI1-01-PRO-251-2026-07-Danny Patricio Bermeo Ochoa.pdf')
    assert v == {'codigo': 'UGPA-RGI1-01', 'proceso': 'PRO-251', 'periodo': '2026-07', 'docente': 'Danny Patricio Bermeo Ochoa'}


def test_duplicate_suffix():
    assert engine.parse_filename('UGPA-RGI1-08-PRO-251-2026-07-ANA TORRES (2).pdf')['docente'] == 'ANA TORRES'


@pytest.mark.parametrize('text,date', [
    ('Fecha de elaboración: 05/08/2026', '2026-08-05'),
    ('Quito, a 20 de agosto de 2026', '2026-08-20'),
    ('fecha: 99/08/2026', ''),
])
def test_dates(text, date):
    assert engine.extract_date(text) == date


def test_name_normalized():
    assert engine.teacher_match('SANTIAGO FERNANDO BRITO GONZÁLEZ', 'Docente: Santiago Fernando Brito Gonzalez') == 'Coincide'
    assert engine.teacher_match('JUAN LOPEZ TORRES', 'Ana Perez',) == 'No localizado'


def _pdf(path, text):
    import fitz
    d = fitz.open()
    p = d.new_page()
    p.insert_text((50, 50), text, fontsize=12)
    d.save(str(path))
    d.close()


def test_unsigned_pdf_kept_unchanged(tmp_path, monkeypatch):
    name = 'UGPA-RGI1-01-PRO-251-2026-07-Danny Patricio Bermeo Ochoa.pdf'
    source = tmp_path / name
    _pdf(source, 'Docente: Danny Patricio Bermeo Ochoa  Fecha de elaboracion: 05/08/2026  Firmado electronicamente')
    before = source.read_bytes()
    monkeypatch.setattr(engine, 'check_signatures', lambda path: {'firmas': [], 'firmas_cantidad': 0})
    row = engine.inspect_file(source)
    assert row['firma'] == 'Texto de firma visible'
    assert row['tipo_pdf'] == 'Digital'
    assert row['fecha_documento'] == '2026-08-05'
    assert row['nombre_coincide'] == 'Coincide'
    assert source.read_bytes() == before


def test_scan_cache_and_duplicate_detection(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'docs'
    root.mkdir()
    a = root / 'archivo.pdf'
    b = root / 'copia.pdf'
    _pdf(a, 'Nombre: Texto del archivo digital con una cantidad de palabras suficiente.')
    b.write_bytes(a.read_bytes())
    monkeypatch.setattr(engine, 'check_signatures', lambda path: {'firmas': [], 'firmas_cantidad': 0})
    cache = tmp_path / 'cache.json'
    assert engine.scan(root, cache) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    rows = [e['row'] for e in events if e['event'] == 'item']
    assert len(rows) == 2
    assert rows[1]['duplicado_de'] == str(a)
    assert engine.scan(root, cache) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert all(e['cached'] for e in events if e['event'] == 'item')


def test_xlsx_escaping_and_sheets(tmp_path):
    from zipfile import ZipFile
    target = tmp_path / 'audit.xlsx'
    engine.export_xlsx(target, [{ 'archivo': '=2+3.pdf', 'estado': 'Revisar', 'observaciones': ['Prueba'],
                                  'firmas': [{ 'numero': 1, 'estado': 'Indeterminada' }]}], '/tmp/inspect')
    with ZipFile(target) as z:
        workbook = z.read('xl/workbook.xml').decode()
        assert all(s in workbook for s in ['Documentos', 'Firmas', 'Incidencias'])
        assert '=2+3.pdf' in z.read('xl/sharedStrings.xml').decode()

def test_signature_date_prior_to_document_is_reviewed(tmp_path, monkeypatch):
    name = 'UGPA-RGI1-01-PRO-251-2026-07-Danny Patricio Bermeo Ochoa.pdf'
    path = tmp_path / name
    _pdf(path, 'Docente: Danny Patricio Bermeo Ochoa\nFecha de elaboracion: 05/08/2026')
    monkeypatch.setattr(engine, 'check_signatures', lambda p: {'firmas': [{
        'numero': 1, 'estado': 'Válida', 'firmante': 'Danny Patricio Bermeo Ochoa',
        'fecha_reportada': '2026-01-01T12:00:00', 'sello_tiempo': 'Confiable'
    }], 'firmas_cantidad': 1})
    row = engine.inspect_file(path)
    assert row['fecha_documento'] == '2026-08-05'
    assert row['estado'] == 'Revisar'
    assert any('anterior' in x for x in row['observaciones'])


def test_signed_without_trusted_timestamp_needs_review(tmp_path, monkeypatch):
    name = 'UGPA-RGI1-01-PRO-251-2026-07-Danny Patricio Bermeo Ochoa.pdf'
    path = tmp_path / name
    _pdf(path, 'Docente: Danny Patricio Bermeo Ochoa\nFecha de elaboracion: 05/08/2026')
    monkeypatch.setattr(engine, 'check_signatures', lambda p: {'firmas': [{
        'numero': 1, 'estado': 'Válida', 'firmante': 'Danny Patricio Bermeo Ochoa',
        'fecha_reportada': '2026-08-05T12:00:00', 'sello_tiempo': 'No verificado'
    }], 'firmas_cantidad': 1})
    row = engine.inspect_file(path)
    assert row['estado'] == 'Revisar'
    assert any('sin sello de tiempo confiable' in x for x in row['observaciones'])
