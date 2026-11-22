// The minimal Panels desktop app. It only opens a window, so that CI can prove
// Developer ID signing and notarization before the real shell exists.
const { app, BrowserWindow } = require('electron');

app.whenReady().then(() => {
  const window = new BrowserWindow({ width: 480, height: 320, title: 'Panels' });
  window.loadURL('data:text/html,<title>Panels</title><body style="font:16px -apple-system;padding:2em">Panels</body>');
});

app.on('window-all-closed', () => app.quit());
