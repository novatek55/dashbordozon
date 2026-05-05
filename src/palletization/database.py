"""
База данных для системы паллетизации
"""
import sqlite3
import os
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional, Tuple

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'palletization.db')


def get_connection():
    """Получить соединение с БД"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """Инициализация базы данных"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Справочник товаров (характеристики для паллетизации)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT UNIQUE NOT NULL,  -- артикул
            name TEXT,                  -- название товара
            layer_height REAL NOT NULL, -- высота одного слоя (м)
            items_per_layer INTEGER NOT NULL, -- кол-во штук в слое
            weight_per_item REAL,       -- вес 1 шт (кг)
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Данные поставки по кластерам
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shipment_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL,
            cluster TEXT NOT NULL,      -- название кластера
            quantity INTEGER NOT NULL,  -- количество штук
            shipment_date DATE,         -- дата поставки
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sku) REFERENCES products(sku)
        )
    ''')
    
    # Индексы
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_shipment_cluster ON shipment_items(cluster)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_shipment_sku ON shipment_items(sku)')
    
    conn.commit()
    conn.close()
    print("База данных инициализирована")


# ============ РАБОТА СО СПРАВОЧНИКОМ ТОВАРОВ ============

def add_or_update_product(sku: str, name: str, layer_height: float, 
                          items_per_layer: int, weight_per_item: float = None) -> bool:
    """Добавить или обновить товар в справочнике"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO products (sku, name, layer_height, items_per_layer, weight_per_item, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(sku) DO UPDATE SET
                name = excluded.name,
                layer_height = excluded.layer_height,
                items_per_layer = excluded.items_per_layer,
                weight_per_item = excluded.weight_per_item,
                updated_at = excluded.updated_at
        ''', (sku, name, layer_height, items_per_layer, weight_per_item, datetime.now()))
        conn.commit()
        return True
    except Exception as e:
        print(f"Ошибка при сохранении товара: {e}")
        return False
    finally:
        conn.close()


def get_all_products() -> List[Dict]:
    """Получить все товары из справочника"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM products ORDER BY sku')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_product_by_sku(sku: str) -> Optional[Dict]:
    """Получить товар по артикулу"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM products WHERE sku = ?', (sku,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_product(sku: str) -> bool:
    """Удалить товар из справочника"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM products WHERE sku = ?', (sku,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"Ошибка при удалении товара: {e}")
        return False
    finally:
        conn.close()


def import_products_from_df(df) -> Tuple[int, List[str]]:
    """Импортировать товары из DataFrame"""
    imported = 0
    errors = []
    
    for idx, row in df.iterrows():
        try:
            sku = str(row.get('артикул', '')).strip()
            if not sku or sku.lower() == 'nan':
                continue
                
            # Парсим числовые значения
            layer_height = row.get('СКОЛЬКО В ПАЛЛЕТЕ ЗАНИМАЕТ ВЫСОТЫ')
            items_per_layer = row.get('СКОЛЬКО В ОДИН СЛОЙ ЗАХОДИТ')
            weight = row.get('ВЕС 1шт')
            
            # Пропускаем если нет обязательных данных
            if pd.isna(layer_height) or pd.isna(items_per_layer):
                errors.append(f"{sku}: отсутствуют обязательные поля")
                continue
            
            # Имя товара - всё что после пробела в артикуле
            name_parts = sku.split(' ', 1)
            name = name_parts[1] if len(name_parts) > 1 else sku
            
            if add_or_update_product(
                sku=sku,
                name=name,
                layer_height=float(layer_height),
                items_per_layer=int(items_per_layer),
                weight_per_item=float(weight) if not pd.isna(weight) else None
            ):
                imported += 1
            else:
                errors.append(f"{sku}: ошибка сохранения")
                
        except Exception as e:
            errors.append(f"Строка {idx}: {str(e)}")
    
    return imported, errors


# ============ РАБОТА С ДАННЫМИ ПОСТАВКИ ============

def add_shipment_item(sku: str, cluster: str, quantity: int, shipment_date: str = None) -> bool:
    """Добавить позицию в поставку"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO shipment_items (sku, cluster, quantity, shipment_date)
            VALUES (?, ?, ?, ?)
        ''', (sku, cluster, quantity, shipment_date or datetime.now().date()))
        conn.commit()
        return True
    except Exception as e:
        print(f"Ошибка при добавлении позиции: {e}")
        return False
    finally:
        conn.close()


def clear_shipment():
    """Очистить все данные поставки"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM shipment_items')
    conn.commit()
    conn.close()


def get_shipment_items() -> List[Dict]:
    """Получить все позиции поставки"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM shipment_items ORDER BY cluster, sku')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_shipment_by_cluster() -> Dict[str, List[Dict]]:
    """Получить поставку сгруппированную по кластерам"""
    items = get_shipment_items()
    result = {}
    for item in items:
        cluster = item['cluster']
        if cluster not in result:
            result[cluster] = []
        result[cluster].append(item)
    return result


def get_missing_products_in_catalog() -> List[str]:
    """Получить список артикулов в поставке, которых нет в справочнике"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT s.sku 
        FROM shipment_items s
        LEFT JOIN products p ON s.sku = p.sku
        WHERE p.sku IS NULL
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]


# Инициализация при импорте
if __name__ == '__main__':
    init_database()
