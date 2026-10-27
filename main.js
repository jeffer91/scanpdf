'use strict';
const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

let win = null;
let selectedFolder = null;
let currentChild = null;
let lastRows = [];
let stderrText = '';
const RESULTS_NAME = 'last-results.json';

function resultsPath() { return path.join(app.getPath('userData'), RESULTS_NAME); }
function cachePath() { return path.join(app.getPath('userData'), 'analysis-cache.json'); }
function engineCommand() {
  if (app.isPackaged) return { command: path.join(process.resourcesPath, 'bin', 'scanpdf-engine.exe'), prefix: [] };
  return { command: process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3'), prefix: [path.join(__dirname, 'backend', 'engine.py')] };
}
function makeWindow() {
  win = new BrowserWindow({
    width: 1220, height: 820, minWidth: 900, minHeight: 630,
    title: 'ScanPDF', backgroundColor: '#f7f9fc', show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true }
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  win.once('ready-to-show', () => win.show());
}
app.whenReady().then(() => {
  makeWindow();
  app.on('activate', () => { if (!BrowserWindow.getAllWindows().length) makeWindow(); });
});
app.on('window-all-closed', () => { if (currentChild) currentChild.kill(); if (process.platform !== 'darwin') app.quit(); });

ipcMain.handle('folder:pick', async () => {
  if (currentChild) return { error: 'Finaliza el análisis actual antes de cambiar de carpeta.' };
  const selected = await dialog.showOpenDialog(win, { properties: ['openDirectory'], title: 'Seleccionar carpeta con PDF' });
  if (selected.canceled || !selected.filePaths[0]) return null;
  selectedFolder = selected.filePaths[0];
  lastRows = [];
  return { folder: selectedFolder };
});

ipcMain.handle('results:load', async () => {
  try {
    const saved = JSON.parse(fs.readFileSync(resultsPath(), 'utf8'));
    if (saved && Array.isArray(saved.rows) && typeof saved.folder === 'string') {
      selectedFolder = saved.folder;
      lastRows = saved.rows;
      return saved;
    }
  } catch (_) { /* No saved results */ }
  return { folder: '', rows: [], date: '' };
});

ipcMain.handle('scan:start', async () => {
  if (currentChild) return { error: 'Ya existe un análisis en curso.' };
  if (!selectedFolder || !fs.existsSync(selectedFolder)) return { error: 'Selecciona una carpeta existente.' };
  const { command, prefix } = engineCommand();
  lastRows = [];
  stderrText = '';
  let processObj;
  try {
    processObj = spawn(command, [...prefix, 'scan', '--folder', selectedFolder, '--cache', cachePath()], {
      windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']
    });
  } catch (err) { return { error: err.message }; }
  currentChild = processObj;
  let buffer = '';
  const emit = (channel, payload) => { if (win && !win.isDestroyed()) win.webContents.send(channel, payload); };
  processObj.stdout.setEncoding('utf8');
  processObj.stdout.on('data', data => {
    buffer += data;
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const part = buffer.slice(0, newline); buffer = buffer.slice(newline + 1);
      if (!part.trim()) continue;
      try {
        const event = JSON.parse(part);
        if (event.event === 'item') lastRows.push(event.row);
        emit('scan:event', event);
      } catch (error) { emit('scan:event', { event: 'warning', text: 'No se pudo interpretar un resultado.' }); }
    }
  });
  processObj.stderr.setEncoding('utf8');
  processObj.stderr.on('data', data => { stderrText = (stderrText + data).slice(-15000); });
  processObj.on('error', err => { emit('scan:event', { event: 'error', text: `No se pudo iniciar el motor: ${err.message}` }); });
  processObj.on('close', (code, signal) => {
    const canceled = signal || code === null;
    currentChild = null;
    if (code === 0) {
      try { fs.mkdirSync(app.getPath('userData'), { recursive: true });
        fs.writeFileSync(resultsPath(), JSON.stringify({ folder: selectedFolder, rows: lastRows, date: new Date().toISOString() }), 'utf8');
      } catch (_) { /* Results are still available until close */ }
    }
    emit('scan:finished', {
      success: code === 0, canceled: Boolean(canceled), count: lastRows.length,
      error: code === 0 ? '' : (canceled ? 'Análisis detenido.' : (stderrText.trim().slice(-900) || `El motor terminó con código ${code}.`))
    });
  });
  return { started: true };
});

ipcMain.handle('scan:cancel', () => {
  if (!currentChild) return false;
  currentChild.kill();
  return true;
});

ipcMain.handle('pdf:open', async (_evt, filePath) => {
  if (typeof filePath !== 'string' || !selectedFolder) return 'Ruta inválida';
  try {
    const root = fs.realpathSync(selectedFolder);
    const target = fs.realpathSync(filePath);
    const relative = path.relative(root, target);
    if (relative.startsWith('..') || path.isAbsolute(relative) || path.extname(target).toLowerCase() !== '.pdf') return 'Archivo fuera de la carpeta seleccionada';
    return await shell.openPath(target);
  } catch (err) { return err.message; }
});

ipcMain.handle('results:export', async (_evt, rows) => {
  if (currentChild) return { error: 'Finaliza el análisis antes de exportar.' };
  if (!Array.isArray(rows) || rows.length === 0 || rows.length > 100000) return { error: 'No hay resultados para exportar.' };
  // Never accept file paths for export directly from the renderer.
  const output = await dialog.showSaveDialog(win, {
    title: 'Guardar informe Excel', defaultPath: 'ScanPDF_Auditoria.xlsx',
    filters: [{ name: 'Excel', extensions: ['xlsx'] }]
  });
  if (output.canceled || !output.filePath) return null;
  const { command, prefix } = engineCommand();
  const input = JSON.stringify({ rows: lastRows, folder: selectedFolder });
  return new Promise(resolve => {
    const child = spawn(command, [...prefix, 'export', '--output', output.filePath], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    let errors = '';
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', part => { errors = (errors + part).slice(-1200); });
    child.on('error', error => resolve({ error: error.message }));
    child.on('close', code => resolve(code === 0 ? { saved: output.filePath } : { error: errors || 'No se pudo crear el Excel.' }));
    child.stdin.on('error', () => {});
    child.stdin.end(input, 'utf8');
  });
});
