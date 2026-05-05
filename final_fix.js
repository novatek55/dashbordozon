// Скрипт для проверки работы паллетизации
// Вставьте это в консоль браузера (F12) на странице с отчетом "Поставка"

function testPalletization() {
  console.log('=== ТЕСТ ПАЛЛЕТИЗАЦИИ ===');
  
  // Собираем данные из ВСЕХ таблиц на странице
  const byCluster = {};
  let foundRows = 0;
  
  // Ищем все таблицы
  const allTables = document.querySelectorAll('table');
  console.log('Всего таблиц:', allTables.length);
  
  allTables.forEach((table, tIdx) => {
    console.log(`\nТаблица ${tIdx}:`, table.className || '(без класса)');
    
    const rows = table.querySelectorAll('tr');
    console.log(`  Строк: ${rows.length}`);
    
    rows.forEach((row, rIdx) => {
      const cells = row.querySelectorAll('td, th');
      if (cells.length >= 2) {
        const firstCell = cells[0].textContent.trim();
        const lastCell = cells[cells.length - 1].textContent.trim();
        const quantity = parseInt(lastCell.replace(/\D/g, '')) || 0;
        
        // Проверяем что это похоже на данные поставки
        if (firstCell && quantity > 0) {
          console.log(`  ✓ Строка ${rIdx}: "${firstCell}" = ${quantity}`);
          foundRows++;
          
          if (!byCluster[firstCell]) {
            byCluster[firstCell] = [];
          }
          
          // Ищем SKU в строке или рядом
          let sku = 'unknown';
          const rowHTML = row.innerHTML;
          const skuMatch = rowHTML.match(/(\d+[^<]*)/);
          if (skuMatch) {
            sku = skuMatch[1].trim().substring(0, 50);
          }
          
          byCluster[firstCell].push({
            sku: sku,
            quantity: quantity
          });
        }
      }
    });
  });
  
  console.log('\n=== РЕЗУЛЬТАТ ===');
  console.log('Найдено строк с данными:', foundRows);
  console.log('Кластеров:', Object.keys(byCluster).length);
  console.log('Данные:', byCluster);
  
  return { byCluster, foundRows };
}

// Запускаем
const result = testPalletization();

// Если нашли данные, отправляем на сервер
if (result.foundRows > 0) {
  console.log('\n=== ОТПРАВКА НА СЕРВЕР ===');
  
  // Преобразуем в формат API
  const supplyItems = Object.entries(result.byCluster).map(([cluster, items]) => ({
    offer_id: items[0].sku || 'unknown',
    details: items.map(i => ({
      cluster_name: cluster,
      allocated_supply: i.quantity
    }))
  }));
  
  console.log('Отправляем:', supplyItems);
  
  fetch('/api/supply-plan/pallets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items: supplyItems })
  })
  .then(r => r.json())
  .then(data => {
    console.log('Ответ:', data);
    if (data.success) {
      console.log('✅ Успех! Кластеров:', data.clusters.length);
    }
  })
  .catch(e => console.error('❌ Ошибка:', e));
}
