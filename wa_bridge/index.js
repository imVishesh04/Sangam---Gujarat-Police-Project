/**
 * SANGAM WhatsApp Web bridge
 * --------------------------
 * Links to WhatsApp exactly like WhatsApp Web: scan the QR once with
 * WhatsApp > Linked devices > Link a device. After that the SANGAM backend can
 * send alerts through POST http://127.0.0.1:3005/send {to, text}.
 *
 * - Listens on 127.0.0.1 only (nothing on the venue Wi-Fi can reach it)
 * - Optional shared secret: set WA_BRIDGE_TOKEN here and in SANGAM's .env
 * - Session is remembered in ./.wa_session (no re-scan after restart)
 * - Uses the Chrome or Edge already installed on Windows (no big download)
 */
const fs = require('fs');
const express = require('express');
const qrcode = require('qrcode-terminal');
const { Client, LocalAuth } = require('whatsapp-web.js');

const PORT = Number(process.env.WA_BRIDGE_PORT || 3005);
const TOKEN = process.env.WA_BRIDGE_TOKEN || '';

function findBrowser() {
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  const local = process.env.LOCALAPPDATA || '';
  const candidates = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    local + '\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ];
  return candidates.find((p) => p && fs.existsSync(p)); // undefined -> puppeteer's own browser
}

const browserPath = findBrowser();
console.log(browserPath ? `Using browser: ${browserPath}` : 'Using Puppeteer bundled browser');

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: './.wa_session' }),
  puppeteer: {
    // WA_SHOW_BROWSER=1 opens a real WhatsApp Web window (clearer QR, visible errors)
    headless: process.env.WA_SHOW_BROWSER !== '1',
    executablePath: browserPath,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  },
});

let state = 'starting';
client.on('qr', (qr) => {
  state = 'waiting_for_qr_scan';
  console.log('\n=== Scan this QR: WhatsApp > Settings > Linked devices > Link a device ===\n');
  qrcode.generate(qr, { small: true });
});
client.on('authenticated', () => { state = 'authenticated'; console.log('Authenticated - loading chats...'); });
client.on('ready', () => { state = 'ready'; console.log(`\n✅ WhatsApp bridge READY on http://127.0.0.1:${PORT}\n`); });
client.on('auth_failure', (m) => {
  state = 'auth_failure';
  console.error('Auth failure:', m, '\n-> Close this window, delete the .wa_session and .wwebjs_cache folders, start again.');
});
client.on('loading_screen', (pct, msg) => console.log(`Loading WhatsApp ${pct}% ${msg || ''}`));
client.on('disconnected', (r) => { state = 'disconnected'; console.error('Disconnected:', r, '- restart the bridge'); });
client.initialize().catch((e) => { state = 'error'; console.error('Could not start WhatsApp Web:', e.message, '\n-> Check internet, then close and restart the bridge.'); });

const app = express();
app.use(express.json({ limit: '64kb' }));

app.get('/status', (req, res) => res.json({ state, ready: state === 'ready' }));

app.post('/send', async (req, res) => {
  if (TOKEN && req.get('X-Bridge-Token') !== TOKEN) return res.status(401).json({ error: 'bad bridge token' });
  if (state !== 'ready') return res.status(503).json({ error: `WhatsApp not ready (${state}) - scan the QR in the bridge window` });
  const digits = String((req.body && req.body.to) || '').replace(/\D/g, '');
  const text = String((req.body && req.body.text) || '');
  if (!digits || !text) return res.status(400).json({ error: 'to and text are required' });
  try {
    const id = await client.getNumberId(digits);
    if (!id) return res.status(404).json({ error: `${digits} is not a WhatsApp number` });
    // sendSeen:false avoids a known WhatsApp Web "mark as seen" crash; some
    // WhatsApp Web versions return nothing even though the message was sent.
    const msg = await client.sendMessage(id._serialized, text, { sendSeen: false });
    return res.json({ ok: true, id: (msg && msg.id && msg.id.id) || 'sent' });
  } catch (e) {
    return res.status(500).json({ error: String((e && e.message) || e) });
  }
});

app.listen(PORT, '127.0.0.1', () => console.log(`Bridge API on http://127.0.0.1:${PORT} (status: /status)`));
