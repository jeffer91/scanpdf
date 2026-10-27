"""Run real packaged executable against a PDF and ensure it exports XLSX."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120, **kwargs)
    if result.returncode:
        raise AssertionError(f'Exit code {result.returncode}: {result.stderr[-1500:]}')
    return result.stdout


def main(exe: str):
    engine = str(Path(exe).resolve())
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        folder = root / 'pdf'
        folder.mkdir()
        pdf = folder / 'UGPA-RGI1-01-PRO-251-2026-07-Danny Patricio Bermeo Ochoa.pdf'
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((50, 60), 'Docente: Danny Patricio Bermeo Ochoa', fontsize=13)
            page.insert_text((50, 86), 'Fecha de elaboracion: 05/08/2026', fontsize=13)
            page.insert_text((50, 110), 'Informe academico institucional sin firmas digitales', fontsize=13)
            doc.save(str(pdf))
        original_bytes = pdf.read_bytes()
        stdout = run([engine, 'scan', '--folder', str(folder), '--cache', str(root / 'cache.json')])
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        rows = [e['row'] for e in events if e.get('event') == 'item']
        assert len(rows) == 1, f'Engine failed to return the PDF: {events}'
        assert rows[0]['tipo_pdf'] == 'Digital', rows
        assert rows[0]['firma'] == 'Sin firma digital', rows
        assert rows[0]['fecha_documento'] == '2026-08-05', rows
        assert original_bytes == pdf.read_bytes(), 'The original PDF was changed'
        out = root / 'Informe.xlsx'
        data = json.dumps({'rows': rows, 'folder': str(folder)}, ensure_ascii=False)
        run([engine, 'export', '--output', str(out)], input=data)
        assert out.is_file() and out.stat().st_size > 1000, 'Excel export failed'
        print('Smoke test passed: Windows EXE, PDF, signatures, read-only and XLSX')


if __name__ == '__main__':
    main(sys.argv[1])
