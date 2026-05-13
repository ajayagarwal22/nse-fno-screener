const { app, BrowserWindow, Tray, Menu, nativeImage, Notification, shell, ipcMain } = require('electron');
const path = require('path');

// DB is one level up from desktop/
const DB_PATH = path.resolve(__dirname, '..', 'screener_trades.db');

let mainWindow = null;
let tray = null;
let pollTimer = null;
let prevState = {};   // trade_id → { status, outcome }

// ── DB query ──────────────────────────────────────────────────────────────────

function queryDB() {
  try {
    const Database = require('better-sqlite3');
    const db = new Database(DB_PATH, { readonly: true, fileMustExist: true });

    const trades = db.prepare(`
      SELECT t.id, t.symbol, t.direction, t.strike, t.option_type, t.expiry,
             t.entry_premium, t.entry_spot, t.entry_time,
             t.exit_premium, t.exit_spot, t.exit_time,
             t.exit_reason, t.pnl_points, t.pnl_percent, t.outcome, t.status,
             s.grade, s.confidence as gate_score, s.htf_trend, s.divergence
      FROM trades t LEFT JOIN signals s ON t.signal_id = s.id
      ORDER BY t.id DESC LIMIT 100
    `).all();

    const overall = db.prepare(`
      SELECT total_trades, wins, losses, win_rate_pct, avg_pnl_pct,
             avg_win_pct, avg_loss_pct, total_pnl_pct
      FROM accuracy_overall
    `).get() || {};

    const extras = db.prepare(`
      SELECT
        SUM(pnl_points)  AS total_pnl_points,
        COUNT(*) FILTER (WHERE status IN ('ACTIVE','WATCHING')) AS active_count
      FROM trades WHERE status != 'SKIPPED'
    `).get() || {};

    const byGrade = db.prepare(`
      SELECT * FROM accuracy_by_grade ORDER BY grade
    `).all();

    db.close();

    return {
      trades,
      summary: { ...overall, ...extras },
      byGrade,
    };
  } catch (err) {
    return { trades: [], summary: {}, byGrade: [], error: err.message };
  }
}

// ── Notifications ─────────────────────────────────────────────────────────────

function notify(title, body) {
  if (Notification.isSupported()) {
    new Notification({ title, body, silent: false }).show();
  }
}

function checkStateChanges(trades) {
  for (const t of trades) {
    const prev = prevState[t.id];
    if (!prev) {
      if (t.status === 'ACTIVE' || t.status === 'WATCHING') {
        notify(
          `Trade Entered — ${t.symbol}`,
          `${t.direction} ${t.strike}${t.option_type} @ ₹${t.entry_premium}`
        );
      }
    } else if (prev.status !== 'CLOSED' && t.status === 'CLOSED') {
      const pnl = t.pnl_points != null
        ? `${t.pnl_points >= 0 ? '+' : ''}${t.pnl_points.toFixed(2)} pts`
        : '';
      const pct = t.pnl_percent != null
        ? ` (${t.pnl_percent >= 0 ? '+' : ''}${t.pnl_percent.toFixed(1)}%)`
        : '';
      notify(
        `${t.outcome || 'Closed'} — ${t.symbol}`,
        `${t.exit_reason} · ${pnl}${pct}`
      );
    }
    prevState[t.id] = { status: t.status, outcome: t.outcome };
  }
}

// ── Poll loop ─────────────────────────────────────────────────────────────────

function poll() {
  const data = queryDB();
  checkStateChanges(data.trades);

  // Update tray tooltip
  if (tray) {
    const active = data.summary.active_count || 0;
    const wr = data.summary.win_rate_pct != null
      ? `${data.summary.win_rate_pct.toFixed(0)}%`
      : '—';
    tray.setToolTip(`Paper Trader  Active: ${active}  WR: ${wr}`);
    tray.setTitle(active > 0 ? `${active}` : '');
  }

  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('trade-update', data);
  }
}

// ── Window ────────────────────────────────────────────────────────────────────

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: '#080b12',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 16, y: 16 },
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    title: 'Paper Trader — NSE F&O',
    show: false,
  });

  mainWindow.loadFile(path.join(__dirname, 'index.html'));

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    poll(); // immediate first paint
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

// ── Tray ──────────────────────────────────────────────────────────────────────

function createTray() {
  // 16×16 transparent icon — Electron draws the menu bar title via setTitle
  const icon = nativeImage.createEmpty();
  tray = new Tray(icon);
  tray.setToolTip('Paper Trader');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Show Window', click: () => { if (mainWindow) mainWindow.show(); else createWindow(); } },
    { type: 'separator' },
    { label: 'Quit', click: () => app.quit() },
  ]));
  tray.on('click', () => {
    if (!mainWindow) { createWindow(); return; }
    mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show();
  });
}

// ── IPC ───────────────────────────────────────────────────────────────────────

ipcMain.on('open-external', (_, url) => shell.openExternal(url));

// ── App lifecycle ─────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  createWindow();
  createTray();
  poll();
  pollTimer = setInterval(poll, 3000);
});

app.on('activate', () => {
  if (!mainWindow) createWindow();
  else mainWindow.show();
});

app.on('window-all-closed', () => {
  // Keep running in tray on macOS
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (pollTimer) clearInterval(pollTimer);
});
