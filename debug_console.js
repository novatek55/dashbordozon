// ВСТАВЬТЕ ЭТОТ КОД В КОНСОЛЬ БРАУЗЕРА (F12) и нажмите Enter

function debugTableData() {
  console.clear();
  console.log('=== ОТЛАДКА ТАБЛИЦ ===\n');
  
  const allTables = document.querySelectorAll('table');
  console.log('1. Всего таблиц найдено:', allTables.length);
  
  let totalRows = 0;
  let validRows = 0;
  
  allTables.forEach((table, tIdx) => {
    console.log(`\n--- Таблица ${tIdx} ---`);
    console.log('   Класс:', table.className || '(нет)');
    
    const rows = table.querySelectorAll('tr');
    console.log('   Всего строк (tr):', rows.length);
    
    rows.forEach((row, rIdx) => {
      const cells = row.querySelectorAll('td');
      
      if (cells.length > 0) {
        console.log(`   Строка ${rIdx}: ${cells.length} ячеек`);
        
        // Выводим содержимое каждой ячейки
        cells.forEach((cell, cIdx) => {
          const text = cell.textContent.trim();
          console.log(`      [${cIdx}] "${text.substring(0, 40)}"`);
        });
        
        // Проверяем условия
        const firstCell = cells[0].textContent.trim();
        const lastCell = cells[cells.length - 1].textContent.trim();
        const quantity = parseInt(lastCell.replace(/\D/g, '')) || 0;
        
        console.log(`      -> firstCell: "${firstCell}" (length: ${firstCell.length})`);
        console.log(`      -> lastCell: "${lastCell}" -> quantity: ${quantity}`);
        
        if (firstCell && quantity > 0) {
          console.log('      ✅ ПОДХОДИТ!');
          validRows++;
        } else {
          console.log('      ❌ Не подходит: firstCell пустой или quantity=0');
        }
        
        totalRows++;
      }
    });
  });
  
  console.log('\n=== ИТОГО ===');
  console.log('Всего строк с td:', totalRows);
  console.log('Подходящих строк:', validRows);
  
  return { totalRows, validRows };
}

// Запускаем
debugTableData();
