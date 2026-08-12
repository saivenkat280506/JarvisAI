const { app, BrowserWindow, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');

let mainWindow;
let pythonProcess = null;
/** Only true when THIS Electron process spawned the Python backend. */
let backendOwnedByUs = false;

const BACKEND_PORT = 8000;
// Prefer local UI for demos/judges; cloud only when packaged without local override
const WEB_URL = process.env.JARVIS_WEB_URL
  || (app.isPackaged ? (process.env.JARVIS_USE_CLOUD === '1'
      ? 'https://jarvis-blue-five.vercel.app'
      : 'http://127.0.0.1:3000')
    : 'http://127.0.0.1:3000');

// Launcher / external process already owns backend — never spawn or kill it.
const EXTERNAL_BACKEND =
  process.env.JARVIS_EXTERNAL_BACKEND === '1' ||
  process.env.JARVIS_EXTERNAL_BACKEND === 'true';

app.commandLine.appendSwitch(
  'disable-features',
  'BlockInsecurePrivateNetworkRequests,PrivateNetworkAccessSendPreflights'
);

// Helper to write to log files safely to avoid EPIPE console crashes in packaged windows mode
function logToFile(msg, isError = false) {
  try {
    const logPath = path.join(app.getPath('userData'), isError ? 'error.log' : 'combined.log');
    const timestamp = new Date().toISOString();
    fs.appendFileSync(logPath, `[${timestamp}] ${msg}\n`, 'utf-8');
  } catch (e) {
    // Ignore logging failures
  }
}

/** True if something is already answering on the local backend health endpoint. */
function isBackendHealthy(timeoutMs = 1500) {
  return new Promise((resolve) => {
    const req = http.get(
      {
        hostname: '127.0.0.1',
        port: BACKEND_PORT,
        path: '/health',
        timeout: timeoutMs,
      },
      (res) => {
        res.resume();
        resolve(res.statusCode >= 200 && res.statusCode < 500);
      }
    );
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

function requestBackendShutdown() {
  return new Promise((resolve) => {
    const req = http.request(
      {
        hostname: '127.0.0.1',
        port: BACKEND_PORT,
        path: '/shutdown',
        method: 'POST',
        timeout: 4000,
      },
      (res) => {
        res.resume();
        logToFile(`Backend shutdown endpoint responded: ${res.statusCode}`);
        resolve();
      }
    );

    req.on('error', (err) => {
      logToFile(`Backend shutdown request failed: ${err.message}`, true);
      resolve();
    });

    req.on('timeout', () => {
      req.destroy();
      logToFile('Backend shutdown request timed out.', true);
      resolve();
    });

    req.end();

    setTimeout(resolve, 4500);
  });
}

function terminatePythonProcess() {
  if (!pythonProcess) {
    return;
  }

  logToFile('Stopping Python backend process we spawned...');
  const proc = pythonProcess;
  pythonProcess = null;
  backendOwnedByUs = false;

  try {
    proc.kill('SIGTERM');
  } catch (err) {
    logToFile(`SIGTERM failed: ${err.message}`, true);
  }

  setTimeout(() => {
    try {
      if (!proc.killed) {
        proc.kill('SIGKILL');
        logToFile('Python backend force-killed.');
      }
    } catch (err) {
      logToFile(`Force kill failed: ${err.message}`, true);
    }
  }, 2500);
}

async function stopJarvisBackend() {
  // Critical: never shut down a backend started by JARVIS.bat / the launcher.
  if (!backendOwnedByUs) {
    logToFile('Skipping backend shutdown (backend not owned by this Electron process).');
    return;
  }
  logToFile('JARVIS close — terminating voice, tasks, and background services...');
  await requestBackendShutdown();
  terminatePythonProcess();
}

function startBackend() {
  return new Promise(async (resolve, reject) => {
    try {
      if (EXTERNAL_BACKEND) {
        logToFile('JARVIS_EXTERNAL_BACKEND set — using launcher-managed backend only.');
        const healthy = await isBackendHealthy(2000);
        if (healthy) {
          logToFile('External backend is healthy on port 8000.');
        } else {
          logToFile('External backend not healthy yet; UI will reconnect when ready.', true);
        }
        resolve();
        return;
      }

      // Prefer a real health check over bind-tests (IPv4/IPv6 mismatch on Windows).
      if (await isBackendHealthy(1500)) {
        logToFile(`Backend already healthy on port ${BACKEND_PORT}. Not spawning another.`);
        backendOwnedByUs = false;
        resolve();
        return;
      }

      logToFile('Resolving Python backend paths...');

      let backendDir = path.join(__dirname, 'backend');
      let pythonPath = path.join(backendDir, '.venv', 'Scripts', 'python.exe');
      let scriptPath = path.join(backendDir, 'main.py');

      if (!fs.existsSync(pythonPath)) {
        logToFile('Python not found inside app dir, searching parent workspace...');
        const workspaceDir = path.resolve(__dirname, '..', '..', '..', '..');
        backendDir = path.join(workspaceDir, 'backend');
        pythonPath = path.join(backendDir, '.venv', 'Scripts', 'python.exe');
        scriptPath = path.join(backendDir, 'main.py');
      }

      if (!fs.existsSync(pythonPath)) {
        return reject(new Error(
          `Could not locate Python virtual environment.\n\n` +
          `Expected path:\n${pythonPath}\n\n` +
          `Please ensure you have run setup in the 'backend' folder and that '.venv' is present.`
        ));
      }

      if (!fs.existsSync(scriptPath)) {
        return reject(new Error(`Could not find backend entry point script at:\n${scriptPath}`));
      }

      logToFile(`Spawning Python backend from: ${pythonPath}`);

      pythonProcess = spawn(pythonPath, [scriptPath], {
        shell: false,
        cwd: backendDir,
        env: {
          ...process.env,
          FOR_DISABLE_CONSOLE_CTRL_HANDLER: 'T',
          PYTHONUNBUFFERED: '1'
        }
      });
      backendOwnedByUs = true;

      pythonProcess.on('exit', (code, signal) => {
        logToFile(`Python backend exited code=${code} signal=${signal}`, true);
        pythonProcess = null;
        backendOwnedByUs = false;
      });

      pythonProcess.stdout.on('data', (data) => {
        logToFile(`[Python Backend]: ${data}`);
      });

      pythonProcess.stderr.on('data', (data) => {
        logToFile(`[Python Backend ERROR]: ${data}`, true);
      });

      const pollInterval = setInterval(async () => {
        if (await isBackendHealthy(800)) {
          clearInterval(pollInterval);
          logToFile('Python backend is ready and listening on port 8000!');
          resolve();
        }
      }, 500);

      // Timeout: still open UI so user can see offline state / reconnect
      setTimeout(() => {
        clearInterval(pollInterval);
        logToFile('Backend health wait timed out; loading UI anyway.', true);
        resolve();
      }, 45000);

    } catch (err) {
      reject(err);
    }
  });
}

async function createWindow() {
  try {
    await startBackend();
  } catch (err) {
    dialog.showErrorBox('J.A.R.V.I.S. Backend Startup Failure', err.message);
    app.quit();
    return;
  }

  mainWindow = new BrowserWindow({
    width: 1300,
    height: 850,
    minWidth: 1000,
    minHeight: 650,
    autoHideMenuBar: true,
    title: 'J.A.R.V.I.S. Workspace',
    icon: path.join(__dirname, 'jarvis_icon.png'),
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      // Allow local HTTP API (127.0.0.1:8000) from the UI origin
      webSecurity: false
    }
  });

  mainWindow.loadURL(WEB_URL);

  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    logToFile(`Failed to load ${WEB_URL}: ${errorDescription} (code: ${errorCode})`, true);
    mainWindow.loadURL(`data:text/html,
      <html>
      <body style="display:flex;align-items:center;justify-content:center;height:100vh;margin:0;
        font-family:Segoe UI,sans-serif;background:radial-gradient(ellipse at 50% 20%,#0e2e58,#05070a);color:#f1f5f9;">
        <div style="text-align:center;max-width:420px;padding:24px;">
          <div style="width:64px;height:64px;margin:0 auto 20px;border-radius:50%;
            border:2px solid #38bdf8;box-shadow:0 0 40px #0ea5e955;"></div>
          <h1 style="letter-spacing:0.35em;font-weight:300;margin:0 0 8px;">J.A.R.V.I.S.</h1>
          <p style="color:#94a3b8;margin:0 0 12px;">Waiting for the local interface…</p>
          <p style="font-size:13px;color:#64748b;line-height:1.5;">
            Run <b style="color:#e2e8f0">Run JARVIS</b> to boot backend + UI,<br/>
            then reopen this window.
          </p>
          <p style="font-size:11px;color:#475569;margin-top:16px;">${errorDescription}</p>
        </div>
      </body>
      </html>
    `);
    let tries = 0;
    const retry = setInterval(() => {
      tries += 1;
      if (tries > 20 || !mainWindow) {
        clearInterval(retry);
        return;
      }
      mainWindow.loadURL(WEB_URL);
    }, 1500);
    mainWindow.webContents.once('did-finish-load', () => clearInterval(retry));
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ── Single-instance lock ────────────────────────────────────────────────────
// A second instance MUST NOT run quit handlers that call /shutdown — that was
// killing the live backend while the first window stayed open as "offline".
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  // app.exit skips before-quit / will-quit — backend stays up.
  app.exit(0);
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(createWindow);

  app.on('before-quit', (event) => {
    if (!app.isQuitting) {
      event.preventDefault();
      app.isQuitting = true;
      stopJarvisBackend().finally(() => app.quit());
    }
  });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
      if (!app.isQuitting) {
        app.isQuitting = true;
        stopJarvisBackend().finally(() => app.quit());
      } else {
        app.quit();
      }
    }
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
}
