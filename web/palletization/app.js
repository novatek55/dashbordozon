// API URL
const API_URL = '/api/palletization';

// РўРµРєСѓС‰РµРµ СЃРѕСЃС‚РѕСЏРЅРёРµ
let currentProducts = [];
let currentShipment = [];
let editingProductId = null;
let deepLinkHandled = false;

function handleDeepLinkFromQuery() {
    if (deepLinkHandled) return;
    const params = new URLSearchParams(window.location.search || "");
    const action = (params.get("action") || "").toLowerCase();
    const sku = (params.get("sku") || "").trim();
    const name = (params.get("name") || "").trim();
    if (!action || !sku) return;

    deepLinkHandled = true;

    const productsTabBtn = document.querySelector('.tab[data-tab="products"]');
    if (productsTabBtn) {
        productsTabBtn.click();
    }

    const product = currentProducts.find(p => String(p.sku || "").trim() === sku);
    if (product) {
        editProduct(product.product_id);
        return;
    }
    alert(`Товар ${name || sku} не найден в базе products`);
}

// ============ РРќРР¦РРђР›РР—РђР¦РРЇ ============

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    loadProducts();
    loadShipment();
    initDragAndDrop();
});

function initTabs() {
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const tabId = tab.dataset.tab;
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById(`tab-${tabId}`).classList.add('active');
        });
    });
}

function initDragAndDrop() {
    const uploadArea = document.getElementById('upload-area');

    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.classList.add('dragover');
    });

    uploadArea.addEventListener('dragleave', () => {
        uploadArea.classList.remove('dragover');
    });

    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.classList.remove('dragover');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFile(files[0]);
        }
    });
}

// ============ Р РђР‘РћРўРђ РЎ РњРћР”РђР›Р¬РќР«РњР РћРљРќРђРњР ============

function showModal(modalId) {
    document.getElementById(modalId).classList.add('active');
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('active');
}

// ============ РЎРџР РђР’РћР§РќРРљ РўРћР’РђР РћР’ ============

async function loadProducts() {
    try {
        const response = await fetch(`${API_URL}/products`);
        const data = await response.json();

        if (data.success) {
            currentProducts = data.products;
            renderProducts();
            updateSkuDatalist();
            handleDeepLinkFromQuery();
        }
    } catch (error) {
        console.error('РћС€РёР±РєР° Р·Р°РіСЂСѓР·РєРё С‚РѕРІР°СЂРѕРІ:', error);
    }
}

function renderProducts(filter = '') {
    const container = document.getElementById('products-list');
    const filterLower = String(filter || '').toLowerCase();

    const filtered = currentProducts.filter(p =>
        String(p.offer_id || '').toLowerCase().includes(filterLower) ||
        String(p.sku || '').toLowerCase().includes(filterLower) ||
        String(p.name || '').toLowerCase().includes(filterLower)
    );

    if (filtered.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">рџ“‹</div>
                <p>${filter ? 'Ничего не найдено' : 'Справочник пуст'}</p>
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <table>
            <thead>
                <tr>
                    <th>Артикул</th>
                    <th>SKU</th>
                    <th>Название</th>
                    <th>Высота из БД (м)</th>
                    <th>Штук в слое</th>
                    <th>Вес из БД (кг)</th>
                    <th>Действия</th>
                </tr>
            </thead>
            <tbody>
                ${filtered.map(product => `
                    <tr>
                        <td>${product.offer_id || '-'}</td>
                        <td>${product.sku || '-'}</td>
                        <td>${product.name || '-'}</td>
                        <td>${product.layer_height ?? '-'}</td>
                        <td>${product.items_per_layer ?? 0}</td>
                        <td>${product.weight_per_item ?? '-'}</td>
                        <td>
                            <div class="actions">
                                <button class="action-btn edit" onclick="editProduct('${product.product_id}')">Изменить</button>
                                <button class="action-btn delete" onclick="deleteProduct('${product.product_id}')">Удалить</button>
                            </div>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function updateSkuDatalist() {
    const datalist = document.getElementById('sku-list');
    datalist.innerHTML = currentProducts.map(p =>
        `<option value="${p.offer_id || p.sku}">${p.name || p.offer_id || p.sku}</option>`
    ).join('');
}

function searchProducts() {
    const filter = document.getElementById('product-search').value;
    renderProducts(filter);
}

function ensureHiddenProductIdInput() {
    let input = document.getElementById('product-id');
    if (!input) {
        input = document.createElement('input');
        input.type = 'hidden';
        input.id = 'product-id';
        document.getElementById('product-form')?.prepend(input);
    }
    return input;
}

function showAddProductModal() {
    alert('Товары берутся из основной БД products. Выберите нужный артикул в списке и нажмите редактирование.');
}

function editProduct(productId) {
    const product = currentProducts.find(p => String(p.product_id) === String(productId));
    if (!product) return;

    editingProductId = product.product_id;
    document.getElementById('product-modal-title').textContent = 'Настроить паллетизацию';
    ensureHiddenProductIdInput().value = product.product_id;
    document.getElementById('product-sku').value = product.offer_id || '';
    document.getElementById('product-name').value = product.name || '';
    document.getElementById('product-layer-height').value = product.layer_height ?? '';
    document.getElementById('product-items-per-layer').value = product.items_per_layer ?? 0;
    document.getElementById('product-weight').value = product.weight_per_item ?? '';

    showModal('product-modal');
}

async function saveProduct(event) {
    event.preventDefault();

    const product = {
        product_id: parseInt(ensureHiddenProductIdInput().value),
        items_per_layer: parseInt(document.getElementById('product-items-per-layer').value)
    };

    try {
        if (!product.product_id) {
            alert('Не найден product_id');
            return;
        }
        const response = await fetch(`${API_URL}/products/${product.product_id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(product)
        });

        const data = await response.json();

        if (data.success) {
            closeModal('product-modal');
            loadProducts();
        } else {
            alert('РћС€РёР±РєР° РїСЂРё СЃРѕС…СЂР°РЅРµРЅРёРё С‚РѕРІР°СЂР°');
        }
    } catch (error) {
        console.error('РћС€РёР±РєР°:', error);
        alert('РћС€РёР±РєР° РїСЂРё СЃРѕС…СЂР°РЅРµРЅРёРё С‚РѕРІР°СЂР°');
    }
}

async function deleteProduct(productId) {
    const product = currentProducts.find(p => String(p.product_id) === String(productId));
    if (!confirm(`Сбросить ручной параметр "штук в слое" для "${product?.offer_id || productId}"?`)) return;

    try {
        const response = await fetch(`${API_URL}/products/${productId}`, {
            method: 'DELETE'
        });

        const data = await response.json();

        if (data.success) {
            loadProducts();
        } else {
            alert('РћС€РёР±РєР° РїСЂРё СѓРґР°Р»РµРЅРёРё С‚РѕРІР°СЂР°');
        }
    } catch (error) {
        console.error('РћС€РёР±РєР°:', error);
        alert('РћС€РёР±РєР° РїСЂРё СѓРґР°Р»РµРЅРёРё С‚РѕРІР°СЂР°');
    }
}

// ============ РРњРџРћР Рў РР— EXCEL ============

function showImportModal() {
    document.getElementById('import-result').innerHTML = '';
    showModal('import-modal');
}

function handleFileSelect(event) {
    const file = event.target.files[0];
    if (file) {
        handleFile(file);
    }
}

async function handleFile(file) {
    const resultDiv = document.getElementById('import-result');

    if (!file.name.endsWith('.xlsx')) {
        resultDiv.innerHTML = '<div class="alert alert-danger">РћС€РёР±РєР°: РџРѕРґРґРµСЂР¶РёРІР°СЋС‚СЃСЏ С‚РѕР»СЊРєРѕ С„Р°Р№Р»С‹ .xlsx</div>';
        return;
    }

    const formData = new FormData();
    formData.append('file', file);

    resultDiv.innerHTML = '<div class="alert alert-warning">Р—Р°РіСЂСѓР·РєР°...</div>';

    try {
        const response = await fetch(`${API_URL}/products/import`, {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (data.success) {
            let html = `<div class="alert alert-success">РРјРїРѕСЂС‚РёСЂРѕРІР°РЅРѕ: ${data.imported} С‚РѕРІР°СЂРѕРІ</div>`;

            if (data.errors && data.errors.length > 0) {
                html += `<div class="alert alert-warning">
                    <strong>РџСЂРµРґСѓРїСЂРµР¶РґРµРЅРёСЏ:</strong>
                    <ul style="margin-top: 10px; padding-left: 20px;">
                        ${data.errors.slice(0, 10).map(e => `<li>${e}</li>`).join('')}
                        ${data.errors.length > 10 ? `<li>... Рё РµС‰С‘ ${data.errors.length - 10} РѕС€РёР±РѕРє</li>` : ''}
                    </ul>
                </div>`;
            }

            resultDiv.innerHTML = html;
            loadProducts();
        } else {
            resultDiv.innerHTML = `<div class="alert alert-danger">РћС€РёР±РєР°: ${data.error}</div>`;
        }
    } catch (error) {
        resultDiv.innerHTML = `<div class="alert alert-danger">РћС€РёР±РєР° Р·Р°РіСЂСѓР·РєРё: ${error.message}</div>`;
    }
}

// ============ Р”РђРќРќР«Р• РџРћРЎРўРђР’РљР ============

async function loadShipment() {
    try {
        const response = await fetch(`${API_URL}/shipment`);
        const data = await response.json();

        if (data.success) {
            currentShipment = data.items;
            renderShipment();
        }
    } catch (error) {
        console.error('РћС€РёР±РєР° Р·Р°РіСЂСѓР·РєРё РїРѕСЃС‚Р°РІРєРё:', error);
    }
}

function renderShipment() {
    const container = document.getElementById('shipment-list');

    if (currentShipment.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">рџ“­</div>
                <p>РќРµС‚ РґР°РЅРЅС‹С… Рѕ РїРѕСЃС‚Р°РІРєРµ</p>
                <p style="font-size: 14px; margin-top: 10px;">Р”РѕР±Р°РІСЊС‚Рµ РїРѕР·РёС†РёРё РІСЂСѓС‡РЅСѓСЋ РёР»Рё Р·Р°РіСЂСѓР·РёС‚Рµ РёР· РѕС‚С‡С‘С‚Р° Ozon</p>
            </div>
        `;
        return;
    }

    const byCluster = {};
    currentShipment.forEach(item => {
        if (!byCluster[item.cluster]) {
            byCluster[item.cluster] = [];
        }
        byCluster[item.cluster].push(item);
    });

    container.innerHTML = Object.entries(byCluster).map(([cluster, items]) => `
        <div style="margin-bottom: 20px;">
            <h4 style="margin-bottom: 10px; color: #667eea;">рџ“Ќ ${cluster}</h4>
            <table>
                <thead>
                    <tr>
                        <th>РђСЂС‚РёРєСѓР»</th>
                        <th>РљРѕР»РёС‡РµСЃС‚РІРѕ</th>
                        <th>Р”РµР№СЃС‚РІРёСЏ</th>
                    </tr>
                </thead>
                <tbody>
                    ${items.map(item => `
                        <tr>
                            <td>${item.sku}</td>
                            <td>${item.quantity}</td>
                            <td>
                                <button class="action-btn delete" onclick="deleteShipmentItem(${item.id})">рџ—‘пёЏ</button>
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
    `).join('');
}

function showAddShipmentModal() {
    document.getElementById('shipment-form').reset();
    showModal('shipment-modal');
}

async function saveShipmentItem(event) {
    event.preventDefault();

    const item = {
        sku: document.getElementById('shipment-sku').value.trim(),
        cluster: document.getElementById('shipment-cluster').value.trim(),
        quantity: parseInt(document.getElementById('shipment-quantity').value)
    };

    try {
        const response = await fetch(`${API_URL}/shipment`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(item)
        });

        const data = await response.json();

        if (data.success) {
            closeModal('shipment-modal');
            loadShipment();
        } else {
            alert('РћС€РёР±РєР° РїСЂРё РґРѕР±Р°РІР»РµРЅРёРё РїРѕР·РёС†РёРё');
        }
    } catch (error) {
        console.error('РћС€РёР±РєР°:', error);
        alert('РћС€РёР±РєР° РїСЂРё РґРѕР±Р°РІР»РµРЅРёРё РїРѕР·РёС†РёРё');
    }
}

async function deleteShipmentItem(id) {
    if (!confirm('РЈРґР°Р»РёС‚СЊ СЌС‚Сѓ РїРѕР·РёС†РёСЋ?')) return;
    loadShipment();
}

async function clearShipment() {
    if (!confirm('РћС‡РёСЃС‚РёС‚СЊ РІСЃРµ РґР°РЅРЅС‹Рµ РїРѕСЃС‚Р°РІРєРё?')) return;

    try {
        const response = await fetch(`${API_URL}/shipment`, {
            method: 'DELETE'
        });

        const data = await response.json();

        if (data.success) {
            loadShipment();
        }
    } catch (error) {
        console.error('РћС€РёР±РєР°:', error);
    }
}

// ============ Р РђРЎР§РЃРў РџРђР›Р›Р•РўРР—РђР¦РР ============

async function calculatePallets() {
    const resultContainer = document.getElementById('pallets-result');
    const alertsContainer = document.getElementById('pallet-alerts');

    resultContainer.innerHTML = '<div class="empty-state"><div class="loading"></div><p>Р Р°СЃС‡С‘С‚...</p></div>';
    alertsContainer.innerHTML = '';

    try {
        const response = await fetch(`${API_URL}/pallets/calculate`);
        const data = await response.json();

        if (data.success) {
            renderPallets(data.clusters);

            const missingResponse = await fetch(`${API_URL}/shipment/missing`);
            const missingData = await missingResponse.json();

            if (missingData.success && missingData.missing_products.length > 0) {
                alertsContainer.innerHTML = `
                    <div class="alert alert-warning">
                        <strong>вљ пёЏ Р’РЅРёРјР°РЅРёРµ!</strong> 
                        Р”Р»СЏ СЃР»РµРґСѓСЋС‰РёС… Р°СЂС‚РёРєСѓР»РѕРІ РЅРµС‚ РґР°РЅРЅС‹С… РІ СЃРїСЂР°РІРѕС‡РЅРёРєРµ:
                        ${missingData.missing_products.map(sku => `
                            <span class="badge badge-danger" style="margin-left: 5px;">
                                ${sku}
                            </span>
                        `).join('')}
                    </div>
                `;
            }
        } else {
            resultContainer.innerHTML = '<div class="alert alert-danger">РћС€РёР±РєР° СЂР°СЃС‡С‘С‚Р°</div>';
        }
    } catch (error) {
        console.error('РћС€РёР±РєР°:', error);
        resultContainer.innerHTML = `<div class="alert alert-danger">РћС€РёР±РєР°: ${error.message}</div>`;
    }
}

function renderPallets(clusters) {
    const container = document.getElementById('pallets-result');

    if (clusters.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">рџ“¦</div>
                <p>РќРµС‚ РґР°РЅРЅС‹С… РґР»СЏ СЂР°СЃС‡С‘С‚Р°</p>
                <p style="font-size: 14px; margin-top: 10px;">РЎРЅР°С‡Р°Р»Р° РґРѕР±Р°РІСЊС‚Рµ РїРѕР·РёС†РёРё РІ РїРѕСЃС‚Р°РІРєСѓ</p>
            </div>
        `;
        return;
    }

    container.innerHTML = clusters.map(cluster => `
        <div class="cluster-section">
            <div class="cluster-header">
                рџ“Ќ ${cluster.cluster}
                ${cluster.errors.length > 0 ? `<span class="badge badge-warning" style="margin-left: 10px;">${cluster.errors.length} РѕС€РёР±РѕРє</span>` : ''}
            </div>
            ${cluster.errors.length > 0 ? `
                <div style="background: #fff3cd; padding: 10px 20px; border-bottom: 1px solid #ffc107;">
                    ${cluster.errors.map(e => `<div style="color: #856404; font-size: 13px;">вљ пёЏ ${e}</div>`).join('')}
                </div>
            ` : ''}
            ${cluster.pallets.map((pallet) => `
                <div class="pallet-card">
                    <div class="pallet-header" onclick="togglePallet(${cluster.cluster.replace(/\s/g, '_')}_${pallet.pallet_number})">
                        <div style="display: flex; align-items: center; gap: 15px;">
                            <span style="font-weight: 600;">рџ“¦ РџР°Р»Р»РµС‚Р° ${pallet.pallet_number}</span>
                            <span class="badge badge-success">${pallet.items.length} SKU</span>
                        </div>
                        <div class="pallet-info">
                            <div class="pallet-info-item">
                                <span class="pallet-info-label">Р’С‹СЃРѕС‚Р°</span>
                                <span class="pallet-info-value">${pallet.total_height.toFixed(2)} Рј</span>
                            </div>
                            <div class="pallet-info-item">
                                <span class="pallet-info-label">Р’РµСЃ</span>
                                <span class="pallet-info-value">${pallet.total_weight.toFixed(2)} РєРі</span>
                            </div>
                            <span class="expand-icon" id="icon-${cluster.cluster.replace(/\s/g, '_')}_${pallet.pallet_number}">в–ј</span>
                        </div>
                    </div>
                    <div class="pallet-items" id="pallet-${cluster.cluster.replace(/\s/g, '_')}_${pallet.pallet_number}">
                        <div class="pallet-item" style="font-weight: 600; color: #666; border-bottom: 2px solid #e0e0e0;">
                            <div>Артикул</div>
                            <div>Кол-во</div>
                            <div>Слоёв</div>
                            <div>Высота</div>
                            <div>Вес</div>
                        </div>
                        ${pallet.items.map(item => `
                            <div class="pallet-item">
                                <div>
                                    <div style="font-weight: 500;">${item.offer_id || item.sku}</div>
                                    <div style="font-size: 12px; color: #888;">SKU ${item.sku}</div>
                                </div>
                                <div>${item.quantity} С€С‚</div>
                                <div>${item.layers}</div>
                                <div>${item.height.toFixed(3)} Рј</div>
                                <div>${item.weight.toFixed(2)} РєРі</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `).join('')}
        </div>
    `).join('');
}

function togglePallet(id) {
    const items = document.getElementById(`pallet-${id}`);
    const icon = document.getElementById(`icon-${id}`);

    items.classList.toggle('expanded');
    icon.classList.toggle('expanded');
}
