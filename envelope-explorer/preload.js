// Minimal bridge: the shell exposes only what the page cannot do for itself.
const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("explorerShell", {
  version: process.versions.electron,
  platform: process.platform,
});
