const fs = require('fs');
const path = require('path');
const http = require('http');
const WebSocket = require('ws');

const RELAY_HTTP = process.env.RELAY_HTTP || 'http://127.0.0.1:19000';
const TOKEN = process.env.RELAY_TOKEN || 'codex-browser-relay-dev-token';
const WS_URL = `${RELAY_HTTP.replace('http', 'ws')}/cdp?token=${TOKEN}`;
const CAPTURE_MS = Number(process.env.CAPTURE_MS || 240000);

const OUT_DIR = path.resolve(__dirname, 'exports');
const OUT_FILE = path.join(OUT_DIR, `ozon_ui_flow_capture_${Date.now()}.jsonl`);

const INTERESTING = [
  '/v1/warehouse/fbo/list',
  '/v1/draft/',
  '/v2/draft/',
  '/v1/cluster/list',
];

function httpGetJson(url) {
  return new Promise((resolve, reject) => {
    http
      .get(url, (res) => {
        let body = '';
        res.on('data', (c) => (body += c.toString('utf8')));
        res.on('end', () => {
          try {
            resolve(JSON.parse(body));
          } catch (e) {
            reject(e);
          }
        });
      })
      .on('error', reject);
  });
}

function jsonSafe(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

async function main() {
  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });
  const sink = fs.createWriteStream(OUT_FILE, { flags: 'a', encoding: 'utf8' });

  const tabs = await httpGetJson(`${RELAY_HTTP}/json/list?token=${TOKEN}`);
  const ozonTab =
    tabs.find((t) => String(t.url || '').includes('seller.ozon.ru')) ||
    tabs.find((t) => String(t.url || '').includes('ozon.ru'));
  if (!ozonTab) {
    throw new Error('No seller.ozon.ru tab found. Open Ozon Seller tab first.');
  }

  console.log(`Target tab: ${ozonTab.title} | ${ozonTab.url}`);
  console.log(`Output: ${OUT_FILE}`);
  console.log(`Capture: ${Math.round(CAPTURE_MS / 1000)}s`);

  const ws = new WebSocket(WS_URL);
  const pending = new Map();
  const reqMap = new Map();
  let id = 0;

  function send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const cmdId = ++id;
      pending.set(cmdId, { resolve, reject, method });
      ws.send(JSON.stringify({ id: cmdId, method, params }));
      setTimeout(() => {
        if (pending.has(cmdId)) {
          pending.delete(cmdId);
          reject(new Error(`Timeout for ${method}`));
        }
      }, 15000);
    });
  }

  ws.on('message', async (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw.toString('utf8'));
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
      const { requestId, request, wallTime, type } = msg.params;
      const url = String(request?.url || '');
      if (!INTERESTING.some((p) => url.includes(p))) return;
      reqMap.set(requestId, {
        at: new Date((wallTime || Date.now() / 1000) * 1000).toISOString(),
        type: type || null,
        url,
        method: request?.method || null,
        request_json: jsonSafe(request?.postData || '') || request?.postData || null,
      });
      return;
    }

    if (msg.method === 'Network.responseReceived') {
      const { requestId, response } = msg.params;
      const row = reqMap.get(requestId);
      if (!row) return;
      row.status = response?.status || null;
      row.mimeType = response?.mimeType || null;
      row.response_headers = response?.headers || null;
      return;
    }

    if (msg.method === 'Network.loadingFinished') {
      const { requestId } = msg.params;
      const row = reqMap.get(requestId);
      if (!row) return;
      reqMap.delete(requestId);

      try {
        const bodyRes = await send('Network.getResponseBody', { requestId });
        const text = bodyRes?.base64Encoded
          ? Buffer.from(bodyRes.body || '', 'base64').toString('utf8')
          : String(bodyRes?.body || '');
        row.response_json = jsonSafe(text);
        if (!row.response_json) row.response_text = text.slice(0, 3000);
      } catch (e) {
        row.response_text = `<<body unavailable: ${e.message}>>`;
      }

      sink.write(`${JSON.stringify(row)}\n`);
      console.log(`[capture] ${row.status || '?'} ${row.method || '?'} ${row.url}`);
      return;
    }
  });

  await new Promise((resolve, reject) => {
    ws.on('open', resolve);
    ws.on('error', reject);
  });

  // Switch relay focus to Ozon Seller tab and enable network capture.
  await send('Target.activateTarget', { targetId: ozonTab.id });
  await send('Network.enable');
  await send('Page.enable');

  await new Promise((resolve) => setTimeout(resolve, CAPTURE_MS));

  ws.close();
  sink.end();
  console.log('Capture finished');
}

main().catch((e) => {
  console.error(e.message || e);
  process.exit(1);
});
