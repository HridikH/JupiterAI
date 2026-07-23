/* JARVIS desktop shell.
 *
 * Run:  npm start   (from the repo root; `npm install` once first)
 *
 * Spawns the Python dashboard server (unless one is already running on 7777),
 * waits for it to answer, then opens a native window on it. Quitting the app
 * kills the server it spawned. All UI lives in web/index.html — this file is
 * only process management + window chrome.
 */
const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const URL = "http://127.0.0.1:7777";
let win = null;
let server = null; // set only if WE spawned it

function pythonBin() {
  const venv = path.join(ROOT, ".venv", "bin", "python");
  return fs.existsSync(venv) ? venv : "python3";
}

function ping() {
  return new Promise((resolve) => {
    const req = http.get(URL, (res) => { res.resume(); resolve(true); });
    req.on("error", () => resolve(false));
    req.setTimeout(1000, () => { req.destroy(); resolve(false); });
  });
}

async function ensureServer() {
  if (await ping()) return; // already running (e.g. started manually) — reuse it
  server = spawn(pythonBin(), ["-m", "jarvis.server", "--no-browser"], {
    cwd: ROOT,
    stdio: "inherit", // model download progress bars land in *this* terminal
  });
  server.on("exit", (code) => {
    server = null;
    if (win && !win.isDestroyed() && code !== 0 && code !== null) {
      win.loadURL("data:text/plain,JARVIS server exited (code " + code + "). Check the terminal.");
    }
  });
  for (let i = 0; i < 240; i++) { // up to 2 min — first launch loads models
    if (await ping()) return;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("server did not come up on " + URL);
}

function createWindow() {
  win = new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#06080f",
    titleBarStyle: "hiddenInset", // orb header acts as the title bar
    show: false,
  });
  win.once("ready-to-show", () => win.show());
  win.loadURL(URL);
}

app.whenReady().then(async () => {
  try {
    await ensureServer();
  } catch (e) {
    console.error(e.message);
  }
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => app.quit());
app.on("quit", () => {
  if (server) server.kill("SIGINT");
});
