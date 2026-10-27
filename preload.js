'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('scanPDF', {
  pickFolder: () => ipcRenderer.invoke('folder:pick'),
  startScan: () => ipcRenderer.invoke('scan:start'),
  stopScan: () => ipcRenderer.invoke('scan:cancel'),
  loadResults: () => ipcRenderer.invoke('results:load'),
  exportResults: rows => ipcRenderer.invoke('results:export', rows),
  openPDF: filePath => ipcRenderer.invoke('pdf:open', filePath),
  onEvent: callback => {
    const fn = (_event, payload) => callback(payload);
    ipcRenderer.on('scan:event', fn);
    return () => ipcRenderer.removeListener('scan:event', fn);
  },
  onFinished: callback => {
    const fn = (_event, payload) => callback(payload);
    ipcRenderer.on('scan:finished', fn);
    return () => ipcRenderer.removeListener('scan:finished', fn);
  }
});
