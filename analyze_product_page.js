const WebSocket = require('ws');

const TOKEN = 'codex-browser-relay-dev-token';

async function analyzeProduct() {
    // Пробуем найти страницу товара
    const targets = [
        '1ABE8D45A676236DA78843AEEC492196', // wildberries.ru
        '53E25B889EF0CBCB10C9454D760209F4', // cmp
        '57F64CC10571B655187BF586D7BAC718'  // cmp duplicate
    ];
    
    for (const TARGET_ID of targets) {
        console.log(`\n=== Checking target: ${TARGET_ID} ===\n`);
        
        try {
            const result = await new Promise((resolve, reject) => {
                const ws = new WebSocket(`ws://127.0.0.1:19000/cdp?token=${TOKEN}&targetId=${TARGET_ID}`);
                let timeout = setTimeout(() => { ws.close(); resolve(null); }, 10000);
                
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
                                            url: location.href,
                                            title: document.title,
                                            isProductPage: false,
                                            productInfo: {},
                                            pluginData: [],
                                            competitors: [],
                                            pricing: {},
                                            seo: {},
                                            reviews: {},
                                            fullText: ''
                                        };
                                        
                                        // Проверяем, что это карточка товара
                                        const isProduct = location.href.includes('/catalog/') || 
                                                         location.href.includes('/product/') ||
                                                         document.querySelector('[data-nm]') ||
                                                         document.querySelector('.product-page__header') ||
                                                         document.querySelector('[class*="product"]');
                                        
                                        data.isProductPage = !!isProduct;
                                        
                                        // Информация о товаре
                                        const nameEl = document.querySelector('h1') || 
                                                      document.querySelector('[class*="product__name"]') ||
                                                      document.querySelector('[data-nm]');
                                        data.productInfo.name = nameEl?.innerText?.trim();
                                        
                                        // Цена
                                        document.querySelectorAll('*').forEach(el => {
                                            const text = el.innerText?.trim();
                                            if (text?.match(/[\d\s]+\s*₽/) && text.length < 30) {
                                                if (!data.pricing.prices) data.pricing.prices = [];
                                                data.pricing.prices.push(text);
                                            }
                                        });
                                        
                                        // Артикул
                                        const artMatch = document.body.innerText.match(/Арт\.?\s*(\d+)/i) ||
                                                        document.body.innerText.match(/артикул[\s:]?(\d+)/i);
                                        data.productInfo.sku = artMatch?.[1];
                                        
                                        // Рейтинг и отзывы
                                        const ratingMatch = document.body.innerText.match(/(\d[,\.]\d)/);
                                        if (ratingMatch) data.reviews.rating = ratingMatch[1];
                                        
                                        const reviewMatch = document.body.innerText.match(/(\d+)\s*оцен/i) ||
                                                           document.body.innerText.match(/(\d+)\s*отзыв/i);
                                        if (reviewMatch) data.reviews.count = reviewMatch[1];
                                        
                                        // Данные плагина (ищем таблицы/блоки с позициями и ценами)
                                        const pluginBlocks = [];
                                        document.querySelectorAll('*').forEach(el => {
                                            const text = el.innerText?.trim();
                                            // Ищем характерные признаки плагина аналитики
                                            if (text && (
                                                text.includes('позиция') ||
                                                text.includes('место') ||
                                                text.includes('конкурент') ||
                                                text.includes('цена конкурент') ||
                                                text.includes('средняя цена') ||
                                                text.includes('минимальная цена') ||
                                                text.includes('в корзину') ||
                                                text.match(/#\d+/) ||
                                                text.match(/место\s*\d+/i)
                                            )) {
                                                if (text.length < 500 && text.length > 10) {
                                                    pluginBlocks.push(text);
                                                }
                                            }
                                        });
                                        data.pluginData = [...new Set(pluginBlocks)].slice(0, 30);
                                        
                                        // Ищем таблицы с данными (типично для плагинов)
                                        const tables = [];
                                        document.querySelectorAll('table, [class*="table"], [class*="grid"]').forEach(table => {
                                            const rows = [];
                                            table.querySelectorAll('tr, [class*="row"]').forEach(row => {
                                                const cells = [];
                                                row.querySelectorAll('td, th, [class*="cell"]').forEach(cell => {
                                                    cells.push(cell.innerText?.trim());
                                                });
                                                if (cells.length > 0) rows.push(cells);
                                            });
                                            if (rows.length > 1) tables.push(rows);
                                        });
                                        data.tables = tables.slice(0, 5);
                                        
                                        // Позиции в поиске (если есть)
                                        const positions = [];
                                        document.querySelectorAll('*').forEach(el => {
                                            const text = el.innerText?.trim();
                                            if (text?.match(/позиция[\s:]?\s*\d+/i) ||
                                                text?.match(/место[\s:]?\s*\d+/i) ||
                                                text?.match(/#\s*\d+/)) {
                                                positions.push(text);
                                            }
                                        });
                                        data.seo.positions = [...new Set(positions)].slice(0, 10);
                                        
                                        // Конкуренты (названия других продавцов)
                                        const sellers = [];
                                        document.querySelectorAll('*').forEach(el => {
                                            const text = el.innerText?.trim();
                                            if (text?.includes('продавец') || text?.includes('Продавец')) {
                                                const sellerMatch = text.match(/продавец[\s:]?(.+)/i);
                                                if (sellerMatch) sellers.push(sellerMatch[1]);
                                            }
                                        });
                                        data.competitors = [...new Set(sellers)].slice(0, 10);
                                        
                                        // Полный текст для анализа
                                        data.fullText = document.body.innerText?.substring(0, 8000);
                                        
                                        return data;
                                    })()
                                `,
                                returnByValue: true
                            }
                        }));
                    }, 800);
                });
                
                ws.on('message', (data) => {
                    const msg = JSON.parse(data);
                    if (msg.id === 2 && msg.result) {
                        clearTimeout(timeout);
                        ws.close();
                        resolve(msg.result.result.value);
                    }
                });
                
                ws.on('error', () => {
                    clearTimeout(timeout);
                    resolve(null);
                });
            });
            
            if (result) {
                console.log(JSON.stringify(result, null, 2));
                if (result.isProductPage) {
                    console.log('\n✅ НАЙДЕНА СТРАНИЦА ТОВАРА!');
                    break;
                }
            }
        } catch (e) {
            console.log('Error:', e.message);
        }
    }
}

analyzeProduct();
