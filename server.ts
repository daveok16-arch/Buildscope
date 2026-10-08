import express from 'express';
import http from 'http';
import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import fs from 'fs';

const app = express();
const PORT = parseInt(process.env.PORT || '3000', 10);
const FLASK_PORT = 12000;
const REPO_ROOT = process.cwd();

let flaskProcess: ChildProcess | null = null;
let isFlaskReady = false;
let isShuttingDown = false;
let restartAttempts = 0;

function ensureDataDir() {
  const dataDir = path.join(REPO_ROOT, 'data');
  if (!fs.existsSync(dataDir)) {
    fs.mkdirSync(dataDir, { recursive: true });
  }
}

function resolvePythonBinary(): string {
  const candidates = [
    process.env.PYTHON_BIN,
    '/usr/local/bin/python3',
    '/usr/bin/python3',
    '/usr/local/bin/python',
    '/usr/bin/python',
    'python3',
    'python',
  ].filter(Boolean) as string[];

  for (const candidate of candidates) {
    if (path.isAbsolute(candidate)) {
      try {
        fs.accessSync(candidate, fs.constants.X_OK);
        return candidate;
      } catch {
        // Not executable or not found
      }
    } else {
      return candidate;
    }
  }
  return 'python3';
}

function startFlaskServer() {
  if (isShuttingDown) return;
  ensureDataDir();

  const pythonCmd = resolvePythonBinary();
  const runServerScript = path.join(REPO_ROOT, 'run_server.py');
  const vendorDir = path.join(REPO_ROOT, 'vendor', 'python');
  const vendorBin = path.join(vendorDir, 'bin');

  const pythonPathParts = [path.join(REPO_ROOT, 'src')];
  if (fs.existsSync(vendorDir)) {
    pythonPathParts.unshift(vendorDir);
  }
  if (process.env.PYTHONPATH) {
    pythonPathParts.push(process.env.PYTHONPATH);
  }

  const env = {
    ...process.env,
    PATH: `${vendorBin}:${path.join(REPO_ROOT, 'testenv', 'bin')}:${process.env.PATH || ''}`,
    PYTHONPATH: pythonPathParts.join(path.delimiter),
    SECRET_KEY: process.env.SECRET_KEY || 'buildscope-production-session-key',
    PORT: String(FLASK_PORT),
    HOST: '127.0.0.1',
    OPPINTEL_DATA_DIR: path.join(REPO_ROOT, 'data'),
    OPPINTEL_DB: path.join(REPO_ROOT, 'data', 'oppintel.db'),
    SESSION_COOKIE_SECURE: process.env.SESSION_COOKIE_SECURE || 'false',
  };

  console.log(`[BuildScope Proxy] Starting Python backend via ${pythonCmd} ${runServerScript}...`);

  try {
    flaskProcess = spawn(pythonCmd, [runServerScript], {
      cwd: REPO_ROOT,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    flaskProcess.on('error', (err) => {
      console.error('[BuildScope Proxy] Python backend process spawn error:', err.message);
      isFlaskReady = false;
    });

    flaskProcess.stdout?.on('data', (data) => {
      const msg = data.toString().trim();
      if (msg) console.log(`[Flask] ${msg}`);
    });

    flaskProcess.stderr?.on('data', (data) => {
      const msg = data.toString().trim();
      if (msg) console.error(`[Flask err] ${msg}`);
    });

    flaskProcess.on('exit', (code, signal) => {
      console.log(`[BuildScope Proxy] Python backend exited with code ${code} signal ${signal}`);
      flaskProcess = null;
      isFlaskReady = false;
      if (!isShuttingDown) {
        restartAttempts++;
        const backoffMs = Math.min(1000 * Math.pow(2, restartAttempts - 1), 10000);
        console.log(`[BuildScope Proxy] Scheduling backend restart in ${backoffMs}ms...`);
        setTimeout(() => {
          if (!flaskProcess && !isShuttingDown) startFlaskServer();
        }, backoffMs);
      }
    });
  } catch (err: any) {
    console.error('[BuildScope Proxy] Synchronous spawn exception:', err.message);
  }
}

function ensureFlaskRunning() {
  if (!flaskProcess && !isShuttingDown) {
    startFlaskServer();
  }
}

function checkFlaskHealth(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.request(
      {
        hostname: '127.0.0.1',
        port: FLASK_PORT,
        path: '/healthz',
        method: 'GET',
        timeout: 1500,
      },
      (res) => {
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 500) {
          resolve(true);
        } else {
          resolve(false);
        }
      }
    );
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

async function monitorFlaskHealth() {
  while (!isShuttingDown) {
    const healthy = await checkFlaskHealth();
    if (healthy) {
      if (!isFlaskReady) {
        console.log('[BuildScope Proxy] Python Flask backend is ready and answering!');
        isFlaskReady = true;
        restartAttempts = 0;
      }
      await new Promise((r) => setTimeout(r, 5000));
    } else {
      if (isFlaskReady) {
        console.warn('[BuildScope Proxy] Python backend is no longer responding to /healthz');
        isFlaskReady = false;
      }
      ensureFlaskRunning();
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
}

// 1. Healthz probe endpoint for platform / Cloud Run
app.get('/healthz', (req, res) => {
  if (!isFlaskReady) {
    // Return 200 OK starting state so Cloud Run deployment health check does not fail during container boot
    res.setHeader('Content-Type', 'application/json');
    return res.status(200).json({
      status: 'starting',
      backend: 'python-flask',
      database: 'oppintel.db',
    });
  }

  // Forward to Flask when ready
  proxyToFlask(req, res);
});

function proxyToFlask(req: express.Request, res: express.Response) {
  ensureFlaskRunning();

  const options: http.RequestOptions = {
    hostname: '127.0.0.1',
    port: FLASK_PORT,
    path: req.url,
    method: req.method,
    headers: {
      ...req.headers,
      host: `127.0.0.1:${FLASK_PORT}`,
      'x-forwarded-for': req.headers['x-forwarded-for'] || req.socket.remoteAddress,
      'x-forwarded-proto': 'http',
      'x-forwarded-host': req.headers.host,
    },
  };

  const proxyReq = http.request(options, (proxyRes) => {
    const headers = { ...proxyRes.headers };

    // In AI Studio preview, allow rendering inside the frame
    delete headers['x-frame-options'];
    if (headers['content-security-policy']) {
      headers['content-security-policy'] = (headers['content-security-policy'] as string)
        .replace("frame-ancestors 'none'", "frame-ancestors *")
        .replace("frame-ancestors 'self'", "frame-ancestors *");
    }

    res.writeHead(proxyRes.statusCode || 200, headers);
    proxyRes.pipe(res);
  });

  proxyReq.on('error', (err) => {
    console.error(`[Proxy error] ${req.method} ${req.url}:`, err.message);
    if (!res.headersSent) {
      res.status(502).send(`
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="utf-8">
          <title>BuildScope — Service Initializing</title>
          <meta name="viewport" content="width=device-width, initial-scale=1.0">
          <style>
            body {
              font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
              background-color: #0f172a;
              color: #f8fafc;
              display: flex;
              align-items: center;
              justify-content: center;
              min-height: 100vh;
              margin: 0;
              padding: 24px;
              box-sizing: border-box;
            }
            .card {
              max-width: 480px;
              text-align: center;
              padding: 32px;
              background: #1e293b;
              border: 1px solid #334155;
              border-radius: 12px;
              box-shadow: 0 10px 25px rgba(0,0,0,0.5);
            }
            h1 { font-size: 20px; font-weight: 700; margin-bottom: 12px; color: #38bdf8; }
            p { font-size: 14px; line-height: 1.6; color: #94a3b8; margin-bottom: 20px; }
            .spinner {
              display: inline-block;
              width: 32px;
              height: 32px;
              border: 3px solid rgba(56, 189, 248, 0.2);
              border-top-color: #38bdf8;
              border-radius: 50%;
              animation: spin 1s linear infinite;
            }
            @keyframes spin { to { transform: rotate(360deg); } }
          </style>
          <script>
            setTimeout(() => window.location.reload(), 2000);
          </script>
        </head>
        <body>
          <div class="card">
            <div class="spinner"></div>
            <h1>BuildScope Intelligence Engine</h1>
            <p>The construction opportunity platform is preparing the database and warming up. This page will automatically refresh in a moment.</p>
          </div>
        </body>
        </html>
      `);
    }
  });

  req.pipe(proxyReq);
}

// 2. Transparent HTTP Reverse Proxy for all other routes
app.use((req, res) => {
  proxyToFlask(req, res);
});

async function main() {
  // Start backend server
  startFlaskServer();

  // Start background health monitor
  monitorFlaskHealth().catch(console.error);

  // Bind server immediately so Cloud Run port probing succeeds without timeout
  const server = app.listen(PORT, '0.0.0.0', () => {
    console.log(`[BuildScope] Server running on http://0.0.0.0:${PORT}`);
  });

  const shutdown = () => {
    isShuttingDown = true;
    console.log('[BuildScope Proxy] Shutting down...');
    if (flaskProcess) {
      flaskProcess.kill('SIGTERM');
    }
    server.close(() => {
      process.exit(0);
    });
  };

  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

main().catch(console.error);
