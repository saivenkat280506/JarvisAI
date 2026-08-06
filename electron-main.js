const { app, BrowserWindow, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');
const net = require('net');
const fs = require('fs');

let mainWindow;
let pythonProcess;
const BACKEND_PORT = 8000;
const WEB_URL = process.env.JARVIS_WEB_URL
  || (app.isPackaged ? 'https://jarvis-blue-five.vercel.app' : 'http://localhost:3000');

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

// Helper to check if local port 8000 is occupied
function isPortInUse(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', (err) => {
      if (err.code === 'EADDRINUSE') {
        resolve(true);
      } else {
        resolve(false);
      }
    });
    server.once('listening', () => {
      server.close();
      resolve(false);
    });
    server.listen(port);
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

  logToFile('Stopping Python backend process...');
  const proc = pythonProcess;
  pythonProcess = null;

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
  logToFile('JARVIS close — terminating voice, tasks, and background services...');
  await requestBackendShutdown();
  terminatePythonProcess();
}

function startBackend() {
  return new Promise(async (resolve, reject) => {
    try {
      const inUse = await isPortInUse(BACKEND_PORT);
      if (inUse) {
        logToFile(`Port ${BACKEND_PORT} is already in use. Assuming local backend is already active.`);
        resolve();
        return;
      }

      logToFile('Resolving Python backend paths...');
      
      // Navigate to find local virtual environment
      let backendDir = path.join(__dirname, 'backend');
      let pythonPath = path.join(backendDir, '.venv', 'Scripts', 'python.exe');
      let scriptPath = path.join(backendDir, 'main.py');

      if (!fs.existsSync(pythonPath)) {
        logToFile('Python not found inside resources/app, searching in parent workspace directory...');
        const workspaceDir = path.resolve(__dirname, '..', '..', '..', '..');
        backendDir = path.join(workspaceDir, 'backend');
        pythonPath = path.join(backendDir, '.venv', 'Scripts', 'python.exe');
        scriptPath = path.join(backendDir, 'main.py');
      }

      // Final validation
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

      // Safely capture output without standard console.log EPIPE crashes
      pythonProcess.stdout.on('data', (data) => {
        logToFile(`[Python Backend]: ${data}`);
      });

      pythonProcess.stderr.on('data', (data) => {
        logToFile(`[Python Backend ERROR]: ${data}`, true);
      });

      // Poll port 8000 to verify when it starts listening
      const pollInterval = setInterval(() => {
        const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/health`, (res) => {
          if (res.statusCode === 200) {
            clearInterval(pollInterval);
            logToFile('Python backend is ready and listening on port 8000!');
            resolve();
          }
        });
        req.on('error', () => {});
        req.end();
      }, 500);

      // Timeout if backend doesn't start in 15 seconds
      setTimeout(() => {
        clearInterval(pollInterval);
        resolve(); // Resolve anyway to load UI
      }, 15000);

    } catch (err) {
      reject(err);
    }
  });
}

async function createWindow() {
  try {
    // Start local Python AI backend server automatically
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
      webSecurity: false // Disables mixed-content security filters to allow loading HTTP API from HTTPS site
    }
  });

  // Load the beautiful, pre-compiled live cloud NextJS frontend URL
  mainWindow.loadURL(WEB_URL);

  // Gracefully handle load failures
  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    logToFile(`Failed to load ${WEB_URL}: ${errorDescription} (code: ${errorCode})`, true);
    mainWindow.loadURL(`data:text/html,
      <html>
      <body style="display:flex;align-items:center;justify-content:center;height:100vh;margin:0;
        font-family:sans-serif;background:#05070a;color:#f1f5f9;">
        <div style="text-align:center;">
          <h1>J.A.R.V.I.S.</h1>
          <p style="color:#94a3b8;">Could not connect to the cloud frontend.</p>
          <p style="font-size:12px;color:#64748b;">${errorDescription}</p>
        </div>
      </body>
      </html>
    `);
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// Prevent multiple instances
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
}

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
