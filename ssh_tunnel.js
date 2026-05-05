const { Client } = require('ssh2');
const net = require('net');

const VDS_HOST = '80.87.203.161';
const VDS_USER = 'root';
const VDS_PASS = 'Dohod100mln$';
const REMOTE_PORT = 8088;
const LOCAL_HOST = '127.0.0.1';
const LOCAL_PORT = 8088;

function startTunnel() {
  const conn = new Client();

  conn.on('ready', () => {
    console.log('[tunnel] Connected to VDS');
    conn.forwardIn('0.0.0.0', REMOTE_PORT, (err) => {
      if (err) {
        console.error('[tunnel] forwardIn error:', err.message);
        conn.end();
        return;
      }
      console.log(`[tunnel] Reverse tunnel active: VDS:${REMOTE_PORT} -> localhost:${LOCAL_PORT}`);
      console.log('[tunnel] Dashboard URL: https://newtekpro.ru/ozon/');
    });
  });

  conn.on('tcp connection', (info, accept, reject) => {
    const channel = accept();
    const sock = net.createConnection(LOCAL_PORT, LOCAL_HOST, () => {
      channel.pipe(sock);
      sock.pipe(channel);
    });
    sock.on('error', (err) => {
      console.error('[tunnel] local connect error:', err.message);
      channel.close();
    });
    channel.on('error', () => sock.destroy());
    sock.on('close', () => channel.close());
    channel.on('close', () => sock.destroy());
  });

  conn.on('error', (err) => {
    console.error('[tunnel] SSH error:', err.message);
  });

  conn.on('close', () => {
    console.log('[tunnel] Connection closed, reconnecting in 5s...');
    setTimeout(startTunnel, 5000);
  });

  conn.on('keyboard-interactive', (_name, _instr, _lang, prompts, finish) => {
    finish(prompts.map(() => VDS_PASS));
  });

  conn.connect({
    host: VDS_HOST,
    username: VDS_USER,
    password: VDS_PASS,
    readyTimeout: 20000,
    keepaliveInterval: 15000,
    keepaliveCountMax: 3,
    tryKeyboard: true,
  });
}

startTunnel();
