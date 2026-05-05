const fs = require('fs');
const path = require('path');
const http = require('http');
const WebSocket = require('ws');

const RELAY_HTTP = process.env.RELAY_HTTP || 'http://127.0.0.1:19000';
const TOKEN = process.env.RELAY_TOKEN || 'codex-browser-relay-dev-token';
const DURATION_MS = Number(process.env.MONITOR_MS || 600000);

function getJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let body = '';
      res.on('data', (c) => (body += c.toString('utf8')));
      res.on('end', () => {
        try { resolve(JSON.parse(body)); } catch (e) { reject(e); }
      });
    }).on('error', reject);
  });
}

function safeJson(text) {
  try { return JSON.parse(text); } catch { return null; }
}

(async () => {
  const outDir = path.resolve(__dirname, '..', 'exports');
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
  const outFile = path.join(outDir, `ozon_step_monitor_${Date.now()}.jsonl`);
  const sink = fs.createWriteStream(outFile, { flags: 'a', encoding: 'utf8' });

  const tabs = await getJson(`${RELAY_HTTP}/json/list?token=${TOKEN}`);
  const seller = tabs.find((t) => String(t.url || '').includes('seller.ozon.ru'));
  if (!seller) throw new Error('No seller.ozon.ru tab found');

  const ws = new WebSocket(`ws://127.0.0.1:19000/cdp?token=${TOKEN}&targetId=${seller.id}`);
  await new Promise((resolve, reject) => { ws.on('open', resolve); ws.on('error', reject); });

  const pending = new Map();
  const reqMap = new Map();
  const wsMap = new Map();
  let id = 0;

  function send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const cid = ++id;
      pending.set(cid, { resolve, reject, method });
      ws.send(JSON.stringify({ id: cid, method, params }));
      setTimeout(() => {
        if (pending.has(cid)) {
          pending.delete(cid);
          reject(new Error(`Timeout ${method}`));
        }
      }, 15000);
    });
  }

  function writeRow(row) {
    sink.write(`${JSON.stringify(row)}\n`);
  }

  ws.on('message', async (raw) => {
    let msg;
    try { msg = JSON.parse(raw.toString('utf8')); } catch { return; }

    if (msg.id) {
      const p = pending.get(msg.id);
      if (!p) return;
      pending.delete(msg.id);
      if (msg.error) p.reject(new Error(JSON.stringify(msg.error)));
      else p.resolve(msg.result || {});
      return;
    }

    if (!msg.method || !msg.params) return;

    if (msg.method === 'Network.webSocketCreated') {
      wsMap.set(msg.params.requestId, msg.params.url || '');
      writeRow({ at: new Date().toISOString(), kind: 'ws-created', url: msg.params.url || '', requestId: msg.params.requestId });
      return;
    }

    if (msg.method === 'Network.webSocketFrameSent' || msg.method === 'Network.webSocketFrameReceived') {
      const requestId = msg.params.requestId;
      const url = wsMap.get(requestId) || '';
      const payload = msg.params.response?.payloadData || '';
      if (!url.includes('seller.ozon.ru') && !payload.includes('supplier') && !payload.includes('dropOff')) return;
      writeRow({
        at: new Date().toISOString(),
        kind: msg.method === 'Network.webSocketFrameSent' ? 'ws-sent' : 'ws-recv',
        url,
        requestId,
        payload: payload.slice(0, 4000),
      });
      return;
    }

    if (msg.method === 'Network.requestWillBeSent') {
      const { requestId, request, wallTime, type } = msg.params;
      const url = String(request?.url || '');
      if (!url.includes('seller.ozon.ru/')) return;
      reqMap.set(requestId, {
        at: new Date((wallTime || Date.now() / 1000) * 1000).toISOString(),
        kind: 'http',
        type: type || null,
        url,
        method: request?.method || null,
        postData: safeJson(request?.postData || '') || request?.postData || null,
      });
      return;
    }

    if (msg.method === 'Network.responseReceived') {
      const { requestId, response } = msg.params;
      const row = reqMap.get(requestId);
      if (!row) return;
      row.status = response?.status || null;
      row.mimeType = response?.mimeType || null;
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
        row.response = safeJson(text) || text.slice(0, 4000);
      } catch (e) {
        row.response = `<<body unavailable: ${e.message}>>`;
      }

      writeRow(row);
      console.log(`[MONITOR] ${row.status || '?'} ${row.method || '?'} ${row.url}`);
    }
  });

  await send('Page.enable');
  await send('Network.enable');
  await send('Runtime.enable');

  const href = await send('Runtime.evaluate', { expression: 'location.href', returnByValue: true });
  const activeUrl = href?.result?.value || 'unknown';
  console.log(`Monitoring started`);
  console.log(`Tab: ${activeUrl}`);
  console.log(`Output: ${outFile}`);
  console.log(`Duration: ${Math.round(DURATION_MS / 1000)} sec`);

  setTimeout(() => {
    try { ws.close(); } catch {}
    try { sink.end(); } catch {}
    console.log('Monitoring finished');
    process.exit(0);
  }, DURATION_MS);
})();
