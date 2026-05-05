const fs = require('fs');
const path = require('path');
const WebSocket = require('ws');

const TARGET_ID = process.env.TARGET_ID || '';
const WS_URL = process.env.CDP_WS_URL || 'ws://127.0.0.1:19000/cdp?token=codex-browser-relay-dev-token';
const DURATION_MS = Number(process.env.CAPTURE_MS || 30000);
const OUT_DIR = path.resolve(__dirname, 'exports');
const OUT_FILE = path.join(OUT_DIR, `ozon_target_capture_${Date.now()}.jsonl`);

const INTERESTING = [
  '/v1/warehouse/fbo/list',
  '/v1/draft/',
  '/v2/draft/',
  '/v1/cluster/list',
  '/supply',
  '/timeslot',
  '/multi-cluster',
];

function safeJson(s) {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

async function main() {
  if (!TARGET_ID) throw new Error('TARGET_ID is required');
  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });
  const out = fs.createWriteStream(OUT_FILE, { flags: 'a', encoding: 'utf8' });

  const ws = new WebSocket(WS_URL);
  let id = 0;
  const pending = new Map();
  const reqMap = new Map();

  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const cmdId = ++id;
      pending.set(cmdId, { resolve, reject });
      ws.send(JSON.stringify({ id: cmdId, method, params }));
      setTimeout(() => {
        if (pending.has(cmdId)) {
          pending.delete(cmdId);
          reject(new Error(`Timeout: ${method}`));
        }
      }, 15000);
    });

  ws.on('message', async (buf) => {
    let msg;
    try {
      msg = JSON.parse(buf.toString('utf8'));
    } catch {
      return;
    }
    if (msg.id) {
      const p = pending.get(msg.id);
      if (!p) return;
      pending.delete(msg.id);
      if (msg.error) p.reject(new Error(JSON.stringify(msg.error)));
      else p.resolve(msg.result || {});
      return;
    }
    if (!msg.method || !msg.params) return;

    if (msg.method === 'Network.requestWillBeSent') {
      const req = msg.params.request || {};
      const url = String(req.url || '');
      if (!INTERESTING.some((s) => url.includes(s))) return;
      reqMap.set(msg.params.requestId, {
        at: new Date().toISOString(),
        url,
        method: req.method || '',
        request_json: safeJson(req.postData || '') || req.postData || null,
      });
      return;
    }

    if (msg.method === 'Network.responseReceived') {
      const row = reqMap.get(msg.params.requestId);
      if (!row) return;
      row.status = msg.params.response?.status || null;
      row.response_headers = msg.params.response?.headers || null;
      return;
    }

    if (msg.method === 'Network.loadingFinished') {
      const row = reqMap.get(msg.params.requestId);
      if (!row) return;
      reqMap.delete(msg.params.requestId);
      try {
        const body = await send('Network.getResponseBody', { requestId: msg.params.requestId });
        const text = body.base64Encoded
          ? Buffer.from(body.body || '', 'base64').toString('utf8')
          : String(body.body || '');
        row.response_json = safeJson(text);
        if (!row.response_json) row.response_text = text.slice(0, 3000);
      } catch (e) {
        row.response_text = `<<unavailable: ${e.message}>>`;
      }
      out.write(`${JSON.stringify(row)}\n`);
      console.log(`[${row.status}] ${row.method} ${row.url}`);
    }
  });

  await new Promise((resolve, reject) => {
    ws.on('open', resolve);
    ws.on('error', reject);
  });

  await send('Target.activateTarget', { targetId: TARGET_ID });
  await send('Network.enable');
  await send('Page.enable');
  await send('Page.reload', { ignoreCache: true });

  await new Promise((r) => setTimeout(r, DURATION_MS));

  out.end();
  ws.close();
  console.log(`saved ${OUT_FILE}`);
}

main().catch((e) => {
  console.error(e.message || e);
  process.exit(1);
});
