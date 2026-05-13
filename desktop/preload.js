const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  onUpdate: (fn) => ipcRenderer.on('trade-update', (_, data) => fn(data)),
  openExternal: (url) => ipcRenderer.send('open-external', url),
});
