const { app, BrowserWindow, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');
const net = require('net');
const fs = require('fs');

let mainWindow;
let pythonProcess;
const BACKEND_PORT = 8000;
const WEB_URL = 'https://jarvis-blue-five.vercel.app';

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
      webSecurity: false // Disables mixed-content security filters to allow loading HTTP API from HTTPS site
    }
  });

  // Load the beautiful, pre-compiled live cloud NextJS frontend URL
  mainWindow.loadURL(WEB_URL);

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  // Gracefully terminate Python background process
  if (pythonProcess) {
    logToFile('Stopping Python backend...');
    pythonProcess.kill('SIGINT');
  }

  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
