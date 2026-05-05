const WebSocket = require('ws');

const TOKEN = 'codex-browser-relay-dev-token';
const TARGET_ID = '1ABE8D45A676236DA78843AEEC492196';

async function getData() {
    const ws = new WebSocket(`ws://127.0.0.1:19000/cdp?token=${TOKEN}&targetId=${TARGET_ID}`);
    
    ws.on('open', () => {
        console.log('Connected');
        
        ws.send(JSON.stringify({
            id: 1,
            method: 'Runtime.evaluate',
            params: {
                expression: 'document.title'
            }
        }));
        
        setTimeout(() => {
            ws.send(JSON.stringify({
                id: 2,
                method: 'Runtime.evaluate',
                params: {
                    expression: `
                        document.body.innerText.substring(0, 10000)
                    `,
                    returnByValue: true
                }
            }));
        }, 500);
    });
    
    ws.on('message', (data) => {
        const msg = JSON.parse(data.toString());
        console.log('ID:', msg.id, 'Result:', msg.result?.result?.value?.substring(0, 2000));
    });
    
    ws.on('error', (e) => console.log('Error:', e.message));
    
    setTimeout(() => ws.close(), 5000);
}

getData();
