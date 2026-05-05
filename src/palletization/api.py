"""
API для системы паллетизации
"""
import os
import sys
import pandas as pd
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

# Добавляем путь к модулю
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import (
    init_database, get_all_products, get_product_by_sku, 
    add_or_update_product, delete_product, import_products_from_df,
    add_shipment_item, clear_shipment, get_shipment_items,
    get_missing_products_in_catalog
)
from calculator import calculate_all_pallets

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '..', '..', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

# ============ API СПРАВОЧНИКА ТОВАРОВ ============

@app.route('/api/products', methods=['GET'])
def get_products():
    """Получить все товары из справочника"""
    products = get_all_products()
    return jsonify({'success': True, 'products': products})


@app.route('/api/products', methods=['POST'])
def create_product():
    """Добавить или обновить товар"""
    data = request.json
    
    success = add_or_update_product(
        sku=data.get('sku', '').strip(),
        name=data.get('name', '').strip(),
        layer_height=float(data.get('layer_height', 0)),
        items_per_layer=int(data.get('items_per_layer', 0)),
        weight_per_item=float(data.get('weight_per_item')) if data.get('weight_per_item') else None
    )
    
    return jsonify({'success': success})


@app.route('/api/products/<sku>', methods=['PUT'])
def update_product(sku):
    """Обновить товар"""
    data = request.json
    
    success = add_or_update_product(
        sku=sku,
        name=data.get('name', '').strip(),
        layer_height=float(data.get('layer_height', 0)),
        items_per_layer=int(data.get('items_per_layer', 0)),
        weight_per_item=float(data.get('weight_per_item')) if data.get('weight_per_item') else None
    )
    
    return jsonify({'success': success})


@app.route('/api/products/<sku>', methods=['DELETE'])
def remove_product(sku):
    """Удалить товар"""
    success = delete_product(sku)
    return jsonify({'success': success})


@app.route('/api/products/import', methods=['POST'])
def import_products():
    """Импортировать товары из Excel файла"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'Файл не найден'})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'Файл не выбран'})
    
    try:
        # Сохраняем файл
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Читаем Excel
        df = pd.read_excel(filepath)
        
        # Импортируем
        imported, errors = import_products_from_df(df)
        
        # Удаляем временный файл
        os.remove(filepath)
        
        return jsonify({
            'success': True,
            'imported': imported,
            'errors': errors
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ============ API ДАННЫХ ПОСТАВКИ ============

@app.route('/api/shipment', methods=['GET'])
def get_shipment():
    """Получить все позиции поставки"""
    items = get_shipment_items()
    return jsonify({'success': True, 'items': items})


@app.route('/api/shipment', methods=['POST'])
def add_shipment():
    """Добавить позицию в поставку"""
    data = request.json
    
    success = add_shipment_item(
        sku=data.get('sku', '').strip(),
        cluster=data.get('cluster', '').strip(),
        quantity=int(data.get('quantity', 0)),
        shipment_date=data.get('shipment_date')
    )
    
    return jsonify({'success': success})


@app.route('/api/shipment/bulk', methods=['POST'])
def add_shipment_bulk():
    """Добавить несколько позиций в поставку"""
    data = request.json
    items = data.get('items', [])
    
    success_count = 0
    errors = []
    
    for item in items:
        try:
            if add_shipment_item(
                sku=item.get('sku', '').strip(),
                cluster=item.get('cluster', '').strip(),
                quantity=int(item.get('quantity', 0)),
                shipment_date=item.get('shipment_date')
            ):
                success_count += 1
            else:
                errors.append(f"{item.get('sku')}: ошибка сохранения")
        except Exception as e:
            errors.append(f"{item.get('sku')}: {str(e)}")
    
    return jsonify({
        'success': True,
        'imported': success_count,
        'errors': errors
    })


@app.route('/api/shipment', methods=['DELETE'])
def clear_shipment_data():
    """Очистить данные поставки"""
    clear_shipment()
    return jsonify({'success': True})


@app.route('/api/shipment/missing', methods=['GET'])
def get_missing():
    """Получить список артикулов без данных в справочнике"""
    missing = get_missing_products_in_catalog()
    return jsonify({'success': True, 'missing_products': missing})


# ============ API ПАЛЛЕТИЗАЦИИ ============

@app.route('/api/pallets/calculate', methods=['GET'])
def calculate_pallets():
    """Рассчитать паллетизацию"""
    results = calculate_all_pallets()
    return jsonify({'success': True, 'clusters': results})


# ============ СТАТИЧЕСКИЕ ФАЙЛЫ ============

@app.route('/palletization/')
def serve_index():
    """Главная страница"""
    web_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'web', 'palletization')
    return send_from_directory(web_dir, 'index.html')


@app.route('/palletization/<path:path>')
def serve_static(path):
    """Статические файлы"""
    web_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'web', 'palletization')
    return send_from_directory(web_dir, path)


# ============ ЗАПУСК ============

if __name__ == '__main__':
    # Инициализируем БД
    init_database()
    
    # Запускаем сервер
    app.run(host='0.0.0.0', port=5001, debug=True)
