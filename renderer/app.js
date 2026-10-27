'use strict';
const $ = id => document.getElementById(id);
const view = { rows: [], folder: '', total: 0, scanning: false, selected: null };
const sanitize = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const preview = value => value ? sanitize(value) : '—';
const folderName = folder => (folder || '').split(/[\\/]/).filter(Boolean).pop() || 'Carpeta seleccionada';
let toastTimeout;
function toast(message) {
  $('toast').textContent = message;
  $('toast').classList.remove('hidden');
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => $('toast').classList.add('hidden'), 6500);
}
function busy(yes) {
  view.scanning = yes;
  $('pickBtn').disabled = yes;
  $('startBtn').classList.toggle('hidden', yes);
  $('stopBtn').classList.toggle('hidden', !yes);
  $('startBtn').disabled = !view.folder || yes;
  $('exportBtn').disabled = !view.rows.length || yes;
  $('progressWrap').classList.toggle('hidden', !yes);
}
function pills(val, forSignature = false) {
  const good = forSignature ? val === 'Válida' : val === 'Correcto';
  const bad = forSignature ? val === 'Inválida' : val === 'Error' || val === 'Incidencia';
  const c = good ? 'ok' : bad ? 'bad' : (val ? 'review' : 'neutral');
  return `<span class="pill ${c}">${preview(val)}</span>`;
}
function filteredRows() {
  const query = $('searchInput').value.trim().toLocaleLowerCase();
  const status = $('statusFilter').value;
  return view.rows.map((row, i) => ({ row, i })).filter(({ row }) => {
    const match = !query || [row.docente, row.archivo, row.codigo, row.proceso].some(s => String(s || '').toLocaleLowerCase().includes(query));
    return match && (!status || row.estado === status);
  });
}
function render() {
  $('folderLabel').textContent = view.folder || 'Selecciona una carpeta con archivos PDF';
  $('folderLabel').title = view.folder;
  $('startBtn').disabled = !view.folder || view.scanning;
  $('exportBtn').disabled = !view.rows.length || view.scanning;
  $('countTotal').textContent = view.rows.length;
  $('countOk').textContent = view.rows.filter(r => r.estado === 'Correcto').length;
  $('countReview').textContent = view.rows.filter(r => r.estado === 'Revisar').length;
  $('countErrors').textContent = view.rows.filter(r => r.estado === 'Error' || r.estado === 'Incidencia').length;
  const list = filteredRows();
  $('subTitle').textContent = view.rows.length ? `${view.rows.length} documentos · ${folderName(view.folder)}` : 'Aquí aparecerán los documentos analizados.';
  $('showCount').textContent = `${list.length} de ${view.rows.length} documentos`;
  $('resultsBody').innerHTML = list.length ? list.map(({ row, i }) => `<tr><td><div class="doc-name">${preview(row.docente)}</div><div class="doc-sub" title="${sanitize(row.archivo)}">${preview(row.archivo)}</div></td><td>${preview(row.fecha_documento)}</td><td>${preview(row.tipo_pdf)}</td><td>${pills(row.firma, true)}</td><td>${pills(row.estado)}</td><td><button type="button" data-index="${i}" class="more">Ver detalle</button></td></tr>`).join('')
    : `<tr><td colspan="6"><div class="empty"><div class="empty-icon">▤</div><strong>${view.rows.length ? 'No hay resultados con este filtro' : 'Tu análisis empieza aquí'}</strong><span>${view.rows.length ? 'Prueba otra búsqueda o selecciona «Todos».' : 'Selecciona una carpeta y pulsa «Analizar carpeta».'}</span></div></td></tr>`;
}
function detailRow(label, val) { return `<div class="detail-row"><span>${sanitize(label)}</span><span>${preview(val)}</span></div>`; }
function openDetails(index) {
  const row = view.rows[index];
  if (!row) return;
  view.selected = row;
  $('detailsTitle').textContent = row.docente || row.archivo;
  let html = `<div class="detail-group"><h3>Documento</h3>${detailRow('Archivo',row.archivo)}${detailRow('Código',row.codigo)}${detailRow('Proceso',row.proceso)}${detailRow('Período',row.periodo)}${detailRow('Docente (archivo)',row.docente)}${detailRow('Docente (contenido)',row.docente_documento)}${detailRow('Coincidencia',row.nombre_coincide)}${detailRow('Fecha documento',row.fecha_documento)}${detailRow('Fecha modificación',row.fecha_modificacion)}${detailRow('Tipo / páginas',`${row.tipo_pdf} · ${row.paginas} páginas`)}${detailRow('Páginas OCR',row.paginas_ocr)}${detailRow('Estado',row.estado)}</div>`;
  html += `<div class="detail-group"><h3>Firmas digitales · ${row.firmas_cantidad || 0}</h3>`;
  if (!row.firmas || row.firmas.length === 0) {
    html += `<div class="detail-note">${row.firma === 'Texto de firma visible' ? 'Se encontró texto relativo a una firma, pero no una firma digital verificable. Una imagen o sello visible no prueba su validez.' : 'No se detectaron firmas digitales incrustadas, o no pudieron inspeccionarse.'}</div>`;
  } else html += row.firmas.map(sig => `<article class="sig-card"><div class="head"><span>Firma ${sig.numero}</span>${pills(sig.estado,true)}</div>${detailRow('Firmante',sig.firmante)}${detailRow('Fecha declarada',sig.fecha_reportada)}${detailRow('Sello tiempo',sig.sello_tiempo)}${detailRow('Integridad',sig.integridad)}${detailRow('Confianza',sig.confianza)}<p>${preview(sig.detalle)}</p></article>`).join('');
  html += '</div><div class="detail-group"><h3>Observaciones</h3>';
  html += row.observaciones && row.observaciones.length ? row.observaciones.map(t => `<div class="detail-note ${row.estado==='Incidencia'||row.estado==='Error'?'error':''}">${preview(t)}</div>`).join('') : '<p style="font-size:12px;color:#78899d">No se identificaron observaciones.</p>';
  html += `</div><div class="detail-group"><h3>Origen</h3>${detailRow('Ruta original',row.ruta)}${detailRow('Duplicado exacto',row.duplicado_de)}</div>`;
  $('detailsContent').innerHTML = html;
  $('overlay').classList.remove('hidden');
  $('closeDetails').focus();
}
function closeDetails() { $('overlay').classList.add('hidden'); view.selected = null; }
$('resultsBody').addEventListener('click', event => {
  const button = event.target.closest('[data-index]');
  if (button) openDetails(Number(button.dataset.index));
});
$('closeDetails').addEventListener('click', closeDetails);
$('closeDetailsBottom').addEventListener('click', closeDetails);
$('overlay').addEventListener('click', e => { if (e.target === $('overlay')) closeDetails(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDetails(); });
$('openPdfBtn').addEventListener('click', async () => {
  if (!view.selected) return;
  const result = await window.scanPDF.openPDF(view.selected.ruta);
  if (result) toast(`No fue posible abrir el PDF: ${result}`);
});
$('searchInput').addEventListener('input', render);
$('statusFilter').addEventListener('change', render);
$('pickBtn').addEventListener('click', async () => {
  const selected = await window.scanPDF.pickFolder();
  if (!selected) return;
  if (selected.error) return toast(selected.error);
  view.folder = selected.folder;
  view.rows = [];
  render();
});
$('startBtn').addEventListener('click', async () => {
  if (!view.folder || view.scanning) return;
  view.rows = [];
  view.total = 0;
  $('progressBar').style.width = '0%';
  $('progressLabel').textContent = 'Leyendo documentos…';
  $('progressCount').textContent = '0 / 0';
  $('progressFile').textContent = 'Preparando análisis';
  busy(true);render();
  const result = await window.scanPDF.startScan();
  if (!result || result.error) { busy(false); toast(result?.error || 'No se pudo iniciar el análisis.'); }
});
$('stopBtn').addEventListener('click', async () => { await window.scanPDF.stopScan(); });
$('exportBtn').addEventListener('click', async () => {
  $('exportBtn').disabled = true;
  const result = await window.scanPDF.exportResults(view.rows);
  $('exportBtn').disabled = false;
  if (result?.saved) toast(`Informe Excel guardado: ${result.saved}`);
  else if (result?.error) toast(result.error);
});
window.scanPDF.onEvent(event => {
  if (event.event === 'start') {
    view.total = event.total;
    $('progressCount').textContent = `0 / ${view.total}`;
    if (!view.total) $('progressLabel').textContent = 'No se encontraron archivos PDF';
  } else if (event.event === 'item') {
    view.rows.push(event.row);
    $('progressCount').textContent = `${event.index} / ${event.total}`;
    $('progressBar').style.width = `${event.total ? (event.index / event.total) * 100 : 100}%`;
    $('progressFile').textContent = `${event.cached ? 'Resultado reutilizado' : 'Procesado'}: ${event.row.archivo}`;
    render();
  } else if (event.event === 'warning' || event.event === 'fatal') toast(event.text);
});
window.scanPDF.onFinished(info => {
  busy(false);render();
  toast(info.success ? `Análisis finalizado. ${info.count} documentos revisados.` : (info.error || 'Análisis interrumpido.'));
});
window.scanPDF.loadResults().then(saved => {
  view.folder = saved.folder || '';
  view.rows = saved.rows || [];
  render();
});
render();
