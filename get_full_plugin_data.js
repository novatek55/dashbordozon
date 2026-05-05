const WebSocket = require('ws');

const TOKEN = 'codex-browser-relay-dev-token';
const TARGET_ID = '1ABE8D45A676236DA78843AEEC492196';

async function getData() {
    const ws = new WebSocket(`ws://127.0.0.1:19000/cdp?token=${TOKEN}&targetId=${TARGET_ID}`);
    
    return new Promise((resolve, reject) => {
        ws.on('open', () => {
            ws.send(JSON.stringify({ id: 1, method: 'Runtime.enable' }));
            
            setTimeout(() => {
                ws.send(JSON.stringify({
                    id: 2,
                    method: 'Runtime.evaluate',
                    params: {
                        expression: `
                            (function() {
                                const data = {
                                    // Основная информация
                                    url: location.href,
                                    title: document.title,
                                    
                                    // Цена
                                    currentPrice: null,
                                    oldPrice: null,
                                    discount: null,
                                    
                                    // Рейтинг и отзывы
                                    rating: null,
                                    reviews: null,
                                    
                                    // Продавец
                                    seller: null,
                                    
                                    // Остаток
                                    stock: null,
                                    
                                    // Данные плагина
                                    pluginData: {
                                        positions: [],
                                        competitors: [],
                                        pricing: {},
                                        stats: {}
                                    },
                                    
                                    // Таблицы (обычно плагины выводят данные в таблицах)
                                    tables: []
                                };
                                
                                // Ищем цены
                                document.querySelectorAll('*').forEach(el => {
                                    const text = el.innerText?.trim();
                                    if (text) {
                                        // Текущая цена
                                        if (text.match(/^\d[\d\s]*\s*₽$/) && !data.currentPrice && text.length < 15) {
                                            data.currentPrice = text;
                                        }
                                        // Скидка
                                        if (text.match(/^-\d+%/)) {
                                            data.discount = text;
                                        }
                                        // Старая цена (зачёркнутая)
                                        if (el.style.textDecoration === 'line-through' || 
                                            el.className?.includes('del') ||
                                            el.className?.includes('old')) {
                                            if (text.match(/[\d\s]+/)) {
                                                data.oldPrice = text;
                                            }
                                        }
                                    }
                                });
                                
                                // Парсим текст страницы для извлечения структурированных данных
                                const allText = document.body.innerText;
                                
                                // Артикул
                                const artMatch = allText.match(/Артикул[\s:]?(\d+)/);
                                if (artMatch) data.sku = artMatch[1];
                                
                                // Продавец
                                const sellerMatch = allText.match(/Продавец\s*([^\n]+)/);
                                if (sellerMatch) data.seller = sellerMatch[1].trim();
                                
                                // Выручка
                                const revenueMatch = allText.match(/Выручка\s*([\d\s]+\s*руб)/);
                                if (revenueMatch) data.pluginData.stats.revenue = revenueMatch[1];
                                
                                // Остаток
                                const stockMatch = allText.match(/Остаток\s*(\d+)\s*шт/);
                                if (stockMatch) data.stock = stockMatch[1];
                                
                                // Продаж в день
                                const salesMatch = allText.match(/Продаж в день\s*([\d,]+)\s*шт/);
                                if (salesMatch) data.pluginData.stats.salesPerDay = salesMatch[1];
                                
                                // Динамика
                                const dynMatch = allText.match(/Динамика выручки\s*([-\d]+%)/);
                                if (dynMatch) data.pluginData.stats.revenueDynamic = dynMatch[1];
                                
                                // Дни в наличии
                                const daysMatch = allText.match(/Был в наличии\s*(\d+)\s*дн/);
                                if (daysMatch) data.pluginData.stats.daysInStock = daysMatch[1];
                                
                                // Позиции по запросам (расширенный поиск)
                                const positionBlocks = [];
                                const lines = allText.split('\\n');
                                
                                lines.forEach((line, i) => {
                                    // Ищем строки с позициями
                                    if (line.match(/место|позиция|#\\d+/i)) {
                                        // Берём контекст - текущую и соседние строки
                                        const context = [
                                            lines[i-2]?.trim(),
                                            lines[i-1]?.trim(),
                                            line.trim(),
                                            lines[i+1]?.trim(),
                                            lines[i+2]?.trim()
                                        ].filter(Boolean).join(' | ');
                                        
                                        if (context.length < 400) {
                                            positionBlocks.push(context);
                                        }
                                    }
                                });
                                data.pluginData.positions = [...new Set(positionBlocks)].slice(0, 15);
                                
                                // Ищем конкурентов по цене
                                const priceBlocks = [];
                                lines.forEach((line, i) => {
                                    if (line.includes('₽') && 
                                        (lines[i-1]?.includes('Дровница') || 
                                         lines[i+1]?.includes('Дровница') ||
                                         line.includes('Дровница'))) {
                                        const context = [
                                            lines[i-1]?.trim(),
                                            line.trim(),
                                            lines[i+1]?.trim()
                                        ].filter(Boolean).join(' ');
                                        if (context.length < 200) {
                                            priceBlocks.push(context);
                                        }
                                    }
                                });
                                data.pluginData.competitors = [...new Set(priceBlocks)].slice(0, 10);
                                
                                // Ищем таблицы
                                document.querySelectorAll('table, [class*="table"]').forEach(table => {
                                    const rows = [];
                                    table.querySelectorAll('tr').forEach(row => {
                                        const cells = [...row.querySelectorAll('td, th')]
                                            .map(c => c.innerText?.trim())
                                            .filter(Boolean);
                                        if (cells.length > 0) rows.push(cells);
                                    });
                                    if (rows.length > 1) data.tables.push(rows);
                                });
                                
                                // Сырые данные плагина (все релевантные блоки)
                                const rawBlocks = [];
                                document.querySelectorAll('div, span, p').forEach(el => {
                                    const text = el.innerText?.trim();
                                    if (text && (
                                        text.includes('место') ||
                                        text.includes('позиция') ||
                                        text.includes('конкурент') ||
                                        text.includes('средняя цена') ||
                                        text.includes('минимальная') ||
                                        text.includes('максимальная') ||
                                        text.includes('выручка') ||
                                        text.includes('продаж')
                                    )) {
                                        if (text.length > 10 && text.length < 500 && !rawBlocks.includes(text)) {
                                            rawBlocks.push(text);
                                        }
                                    }
                                });
                                data.pluginData.raw = rawBlocks.slice(0, 20);
                                
                                return data;
                            })()
                        `,
                        returnByValue: true
                    }
                }));
            }, 1000);
        });
        
        ws.on('message', (data) => {
            const msg = JSON.parse(data);
            if (msg.id === 2 && msg.result) {
                console.log(JSON.stringify(msg.result.result.value, null, 2));
                ws.close();
                resolve();
            }
        });
        
        ws.on('error', reject);
    });
}

getData();
