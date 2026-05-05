const WebSocket = require('ws');

const TOKEN = 'codex-browser-relay-dev-token';
const TARGET_ID = '1ABE8D45A676236DA78843AEEC492196';

async function getData() {
    const ws = new WebSocket(`ws://127.0.0.1:19000/cdp?token=${TOKEN}&targetId=${TARGET_ID}`);
    
    ws.on('open', () => {
        ws.send(JSON.stringify({
            id: 1,
            method: 'Runtime.evaluate',
            params: {
                expression: `
                    (function() {
                        const text = document.body.innerText;
                        const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                        
                        // Ищем запросы - они обычно перед позициями
                        const searchQueries = [];
                        for (let i = 0; i < lines.length; i++) {
                            const line = lines[i];
                            // Проверяем, является ли строка поисковым запросом
                            if (line.match(/ножки|подстолье|опора|стол/i) && 
                                line.length > 5 && line.length < 60 &&
                                !line.includes('₽') &&
                                !line.includes('место') &&
                                !line.includes('Артикул') &&
                                !line.includes('Продавец')) {
                                
                                // Проверяем, что рядом есть позиция
                                const nearby = lines.slice(i+1, i+5).join(' ');
                                if (nearby.includes('место') || nearby.match(/\\d+\s*ч\./)) {
                                    searchQueries.push(line);
                                }
                            }
                        }
                        
                        // Ищем блок с ценами конкурентов подробнее
                        const priceTable = [];
                        let inPriceBlock = false;
                        for (let i = 0; i < lines.length; i++) {
                            const line = lines[i];
                            if (line.includes('Дровница') || line.includes('Подстолье')) {
                                inPriceBlock = true;
                            }
                            if (inPriceBlock && line.includes('₽')) {
                                priceTable.push({
                                    name: lines[i-1] || '',
                                    price: line,
                                    rating: lines[i+1]?.includes('⭐') ? lines[i+1] : ''
                                });
                            }
                            if (priceTable.length > 10) break;
                        }
                        
                        // Все строки содержащие ножки/стол (для поиска запросов)
                        const allQueries = lines.filter(l => 
                            l.match(/ножки|подстолье|опора/i) && 
                            l.length > 10 && 
                            l.length < 80 &&
                            !l.includes('место') &&
                            !l.includes('Артикул')
                        );
                        
                        return {
                            searchQueries: [...new Set(searchQueries)].slice(0, 20),
                            priceTable: priceTable.slice(0, 10),
                            allRelevantLines: [...new Set(allQueries)].slice(0, 20),
                            // Фрагмент с конкурентами
                            competitorSection: lines.slice(
                                lines.findIndex(l => l.includes('Дровница')) - 5,
                                lines.findIndex(l => l.includes('Дровница')) + 30
                            ).join('\\n')
                        };
                    })()
                `,
                returnByValue: true
            }
        }));
    });
    
    ws.on('message', (data) => {
        const msg = JSON.parse(data.toString());
        if (msg.result?.result?.value) {
            console.log(JSON.stringify(msg.result.result.value, null, 2));
        }
    });
    
    ws.on('error', (e) => console.log('Error:', e.message));
    
    setTimeout(() => ws.close(), 5000);
}

getData();
