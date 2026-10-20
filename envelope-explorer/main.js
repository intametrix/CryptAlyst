// Electron shell for The Envelope Explorer.
//
// The app is deliberately thin: the interface is the same web UI the browser
// serves, so there is one implementation and one behaviour. This shell adds a
// native window, a menu, remembered geometry, and a clear offline state when the
// backend is not running.
const { app, BrowserWindow, Menu, shell, dialog } = require("electron");
const path = require("path");
const fs = require("fs");

const TARGET = process.env.EXPLORER_URL || "http://127.0.0.1:8802/";
const isDev = process.argv.includes("--dev");

const stateFile = () => path.join(app.getPath("userData"), "window-state.json");

function loadState() {
  try {
    return JSON.parse(fs.readFileSync(stateFile(), "utf8"));
  } catch (e) {
    return { width: 1600, height: 1000 };
  }
}

function saveState(win) {
  try {
    const b = win.getBounds();
    fs.writeFileSync(stateFile(), JSON.stringify(b));
  } catch (e) {
    /* geometry is a convenience, never a failure */
  }
}

let win;

function createWindow() {
  const state = loadState();
  win = new BrowserWindow({
    width: state.width || 1600,
    height: state.height || 1000,
    x: state.x,
    y: state.y,
    minWidth: 1100,
    minHeight: 700,
    backgroundColor: "#070b11",
    title: "The Envelope Explorer",
    titleBarStyle: "hiddenInset",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  win.once("ready-to-show", () => win.show());
  win.on("resize", () => saveState(win));
  win.on("move", () => saveState(win));
  win.on("closed", () => (win = null));

  // external links open in the browser, never inside the evidence view
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(TARGET.replace(/\/$/, ""))) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });

  go();
}

// go loads the service, or the offline page when it is not reachable.
async function go() {
  if (!win) return;
  try {
    const res = await fetch(TARGET + "api/stats", { signal: AbortSignal.timeout(2500) });
    if (!res.ok) throw new Error("status " + res.status);
    await win.loadURL(TARGET);
  } catch (e) {
    await win.loadFile(path.join(__dirname, "offline.html"));
  }
}

function buildMenu() {
  const template = [
    {
      label: "The Envelope Explorer",
      submenu: [
        { role: "about" },
        { type: "separator" },
        { label: "Reconnect", accelerator: "CmdOrCtrl+R", click: () => go() },
        { label: "Open in browser", click: () => shell.openExternal(TARGET) },
        { type: "separator" },
        { role: "hide" }, { role: "hideOthers" }, { type: "separator" }, { role: "quit" },
      ],
    },
    {
      label: "View",
      submenu: [
        { label: "Feed", accelerator: "CmdOrCtrl+1", click: () => tab("list") },
        { label: "Journey", accelerator: "CmdOrCtrl+2", click: () => tab("journey") },
        { label: "Execution", accelerator: "CmdOrCtrl+3", click: () => tab("execution") },
        { label: "Why", accelerator: "CmdOrCtrl+4", click: () => tab("why") },
        { label: "Gantt", accelerator: "CmdOrCtrl+5", click: () => tab("gantt") },
        { label: "Books", accelerator: "CmdOrCtrl+6", click: () => tab("books") },
        { label: "Health", accelerator: "CmdOrCtrl+7", click: () => tab("health") },
        { type: "separator" },
        { label: "Back", accelerator: "CmdOrCtrl+[", click: () => win && win.webContents.executeJavaScript("goBack()") },
        { type: "separator" },
        { role: "toggleDevTools" }, { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" },
        { role: "togglefullscreen" },
      ],
    },
    {
      label: "Evidence",
      submenu: [
        { label: "Case file for selection", accelerator: "CmdOrCtrl+E", click: () => exportFor("case") },
        { label: "Book report (books view)", accelerator: "CmdOrCtrl+Shift+E", click: () => exportFor("book") },
        { type: "separator" },
        { label: "Print…", accelerator: "CmdOrCtrl+P", click: () => win && win.webContents.print({}) },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function tab(view) {
  if (win) win.webContents.executeJavaScript(`document.querySelector('.tab[data-view="${view}"]').click()`);
}

// exportFor opens the printable page for whatever is selected, in this window so
// the native print dialog is available for Save as PDF.
function exportFor(kind) {
  if (!win) return;
  const script =
    kind === "case"
      ? `(function(){ if (state.sel == null) return "none"; return "/api/case.html?id=" + state.sel; })()`
      : `(function(){ var t = document.querySelector('.tab[data-view="books"]'); if (t) t.click(); return "/api/book.html?engine=" + encodeURIComponent((window.__bookEngine || "grid")); })()`;
  win.webContents.executeJavaScript(script).then((url) => {
    if (!url || url === "none") {
      dialog.showMessageBox(win, { message: "Select an envelope first.", type: "info" });
      return;
    }
    win.loadURL(TARGET.replace(/\/$/, "") + url);
  });
}

app.whenReady().then(() => {
  buildMenu();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
