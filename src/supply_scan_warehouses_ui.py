"""
supply_scan_warehouses_ui.py — Сканирование складов через UI seller.ozon.ru (Playwright)

Для каждого активного кластера в черновике:
  1. Нажимает "Выбрать" → открывает модалку
  2. Перебирает склады МСК/МО — вводит имя в поле поиска
  3. Проверяет табы: "Для коробок" и "Для палет и коробок"
  4. Читает ближайший таймслот из карточки
  5. Собирает данные и возвращает отчёт

Работает через Playwright (OzonBrowser, порт 9223).
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MSK = timezone(timedelta(hours=3))

# Склады МСК/МО для поиска (приоритетные первыми)
MSK_WAREHOUSES_TO_SCAN = [
    "ДОМОДЕДОВО_РФЦ_КРОСС",
    "МО_ЩЕРБИНКА_ХАБ",
    "МО_ВНУКОВО_2_ХАБ",
    "СЦ_РЯБИНОВАЯ_КРОССДОК",
    "МСК_КАВКАЗСКИЙ_2_ХАБ",
    "МО_ЗАМОСКВОРЕЧЬЕ_XD",
    "МСК_ВОЛГОГРАДСКИЙ_3_Х",
    "МО_ДАВЫДОВСКОЕ_ФБС",
    "МО_ТСЦ_НОВАЯ_РИГА",
    "ПЕТРОВСКОЕ_РФЦ_КРОСС",
    "МСК_ЧЕРМЯНСКАЯ_ФБС",
    "МО_ОСТАШКОВСКИЙ_ХАБ",
    "МО_ОСТАШКОВСКИЙ_3_Х",
    "МСК_МОЛЖАНИНОВО_3_ХА",
    "МО_ТСЦ_НИКОЛЬСКОЕ",
    "ХОРУГВИНО_РФЦ_КРОССДОК",
    "ЖУКОВСКИЙ_РФЦ_КРОССДОК",
    "СОФЬИНО_РФЦ_КРОССДОК",
    "ПУШКИНО_1_РФЦ_КРОССДОК",
]


def _log(msg: str) -> None:
    try:
        print(f"  [scan-ui] {msg}", flush=True)
    except UnicodeEncodeError:
        # Fallback: убираем Unicode-символы
        safe = msg.encode("ascii", errors="replace").decode("ascii")
        print(f"  [scan-ui] {safe}", flush=True)


def _normalize_cluster_key(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


async def _get_clusters_from_table(page) -> list[dict[str, Any]]:
    """
    Получить список кластеров из таблицы черновика.
    Использует JS для надёжного чтения DOM.
    """
    data = await page.evaluate("""
        () => {
            const rows = document.querySelectorAll('table tr');
            const result = [];
            for (let i = 0; i < rows.length; i++) {
                const tds = rows[i].querySelectorAll('td');
                if (tds.length < 6) continue;
                const cb = tds[0]?.querySelector('input[type="checkbox"]');
                const nameDiv = tds[1]?.querySelector('div');
                const lastBtn = tds[5]?.querySelector('button');
                const btnText = lastBtn?.textContent?.trim() || '';
                // Извлекаем чистое имя кластера (без "N складов")
                const rawName = nameDiv?.textContent?.trim() || '';
                const name = rawName.replace(/\\d+\\s*склад.*$/i, '').trim();
                result.push({
                    rowIndex: i,
                    name: name,
                    checked: cb?.checked || false,
                    buttonText: btnText.substring(0, 60),
                    needsSelection: btnText === 'Выбрать',
                });
            }
            return result;
        }
    """)
    return data or []


async def _click_select_for_cluster(page, cluster_name: str) -> bool:
    """
    Найти строку кластера по имени, кликнуть кнопку в последнем столбце.
    Использует JS для поиска кнопки, затем Playwright для клика.
    """
    # Через JS находим кнопку и ставим ей временный id
    found = await page.evaluate("""
        (clusterName) => {
            const rows = document.querySelectorAll('table tr');
            for (const row of rows) {
                const tds = row.querySelectorAll('td');
                if (tds.length < 6) continue;
                const cb = tds[0]?.querySelector('input[type="checkbox"]');
                if (!cb || !cb.checked) continue;
                const name = tds[1]?.textContent?.trim() || '';
                if (name.includes(clusterName)) {
                    const btn = tds[tds.length - 1]?.querySelector('button');
                    if (btn) {
                        btn.setAttribute('data-scan-click', 'true');
                        return true;
                    }
                }
            }
            return false;
        }
    """, cluster_name)

    if not found:
        _log(f"Кнопка для '{cluster_name}' не найдена")
        return False

    try:
        btn = page.locator("button[data-scan-click='true']")
        await btn.scroll_into_view_if_needed()
        await btn.click()
        # Убираем атрибут
        await page.evaluate("() => { const b = document.querySelector('button[data-scan-click]'); if (b) b.removeAttribute('data-scan-click'); }")
        return True
    except Exception as e:
        _log(f"Ошибка клика для '{cluster_name}': {e}")
        return False


async def _wait_for_modal(page, timeout: int = 10000) -> bool:
    """Дождаться открытия модалки 'Способ доставки в кластер'."""
    try:
        await page.get_by_text("Способ доставки в кластер").wait_for(timeout=timeout)
        await page.wait_for_timeout(500)
        return True
    except Exception:
        return False


async def _close_modal(page) -> None:
    """Закрыть модальное окно — нажать крестик или Escape."""
    try:
        # Пробуем Escape
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
    except Exception:
        pass


async def _open_cluster_items_modal(page, cluster_name: str) -> bool:
    """Открыть модалку товаров кластера из строки таблицы черновика."""
    found = await page.evaluate(
        """
        (clusterName) => {
            const normalize = (value) => String(value || '').trim().toLowerCase().replace(/\\s+/g, ' ');
            const targetKey = normalize(clusterName);
            const isVisible = (el) => !!el && !!el.offsetParent;
            const rows = document.querySelectorAll('table tr');
            for (const row of rows) {
                const tds = row.querySelectorAll('td');
                if (tds.length < 2) continue;
                const rawName = tds[1]?.textContent?.trim() || '';
                const rowName = normalize(rawName.replace(/\\d+\\s*склад.*$/i, '').trim());
                if (!rowName || (rowName !== targetKey && !rowName.includes(targetKey) && !targetKey.includes(rowName))) continue;
                const target = Array.from(row.querySelectorAll('button,a,[role="button"]'))
                    .filter(isVisible)
                    .find((el) => /товар|заявк/i.test(el.textContent || ''));
                if (target) {
                    target.setAttribute('data-open-cluster-items', 'true');
                    return true;
                }
            }
            return false;
        }
        """,
        cluster_name,
    )
    if not found:
        _log(f"Не нашел кнопку товаров для кластера '{cluster_name}'")
        return False
    try:
        btn = page.locator("[data-open-cluster-items='true']").first
        await btn.scroll_into_view_if_needed()
        await btn.click()
        return True
    except Exception as e:
        _log(f"Ошибка открытия товаров кластера '{cluster_name}': {e}")
        return False
    finally:
        try:
            await page.evaluate(
                "() => document.querySelectorAll('[data-open-cluster-items]').forEach((el) => el.removeAttribute('data-open-cluster-items'))"
            )
        except Exception:
            pass


async def _wait_for_cluster_items_modal(page, timeout: int = 10000) -> bool:
    try:
        await page.get_by_text("Товары для кластера").wait_for(timeout=timeout)
        await page.wait_for_timeout(500)
        return True
    except Exception:
        return False


async def _activate_cluster_request_items_tab(page) -> bool:
    try:
        tab = page.get_by_text("Товары в заявке")
        if await tab.count() > 0:
            await tab.first.click()
            await page.wait_for_timeout(400)
            return True
    except Exception:
        pass
    return False


async def _set_cluster_items_search(page, query: str) -> bool:
    found = await page.evaluate(
        """
        () => {
            const isVisible = (el) => !!el && !!el.offsetParent;
            const nodes = Array.from(document.querySelectorAll('[role="dialog"], [data-testid] div, div'));
            const modal = nodes.find((node) => isVisible(node) && /Товары для кластера/i.test(node.textContent || ''));
            if (!modal) return false;
            const input = Array.from(modal.querySelectorAll('input')).find((inp) => {
                if (!isVisible(inp)) return false;
                const type = String(inp.type || '').toLowerCase();
                return !type || type === 'text' || type === 'search';
            });
            if (!input) return false;
            input.setAttribute('data-cluster-items-search', 'true');
            input.focus();
            return true;
        }
        """
    )
    if not found:
        return False
    try:
        input = page.locator("input[data-cluster-items-search='true']").first
        await input.click()
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Delete")
        if query:
            await page.keyboard.type(query, delay=20)
        await page.wait_for_timeout(700)
        return True
    except Exception:
        return False
    finally:
        try:
            await page.evaluate(
                "() => document.querySelectorAll('input[data-cluster-items-search]').forEach((el) => el.removeAttribute('data-cluster-items-search'))"
            )
        except Exception:
            pass


async def _read_cluster_items_summary(page) -> dict[str, int]:
    data = await page.evaluate(
        """
        () => {
            const isVisible = (el) => !!el && !!el.offsetParent;
            const nodes = Array.from(document.querySelectorAll('[role="dialog"], [data-testid] div, div'));
            const modal = nodes.find((node) => isVisible(node) && /Товары для кластера/i.test(node.textContent || ''));
            if (!modal) return { itemsCount: 0, unitsCount: 0 };
            const text = String(modal.innerText || modal.textContent || '');
            const itemsMatches = [...text.matchAll(/(\\d+)\\s+товар(?:ов|а)?/gi)];
            const unitsMatches = [...text.matchAll(/(\\d+)\\s+штук/gi)];
            return {
                itemsCount: itemsMatches.length ? Number(itemsMatches[itemsMatches.length - 1][1] || 0) : 0,
                unitsCount: unitsMatches.length ? Number(unitsMatches[unitsMatches.length - 1][1] || 0) : 0,
            };
        }
        """
    )
    if not isinstance(data, dict):
        return {"itemsCount": 0, "unitsCount": 0}
    return {
        "itemsCount": int(data.get("itemsCount") or 0),
        "unitsCount": int(data.get("unitsCount") or 0),
    }


async def reconcile_cluster_quantities(
    page,
    requested_clusters: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Снять фактические количества по SKU из модалки `Товары в заявке`."""
    clusters_input = requested_clusters or []
    if not clusters_input:
        return {"clusters": [], "changed_clusters": [], "removed_total": 0}

    all_clusters = await _get_clusters_from_table(page)
    by_name = {
        _normalize_cluster_key(cluster.get("name") or ""): cluster
        for cluster in all_clusters
        if str(cluster.get("name") or "").strip()
    }

    results: list[dict[str, Any]] = []
    changed_clusters: list[str] = []
    removed_total = 0

    for cluster_input in clusters_input:
        cluster_name = str(cluster_input.get("cluster_name") or "").strip()
        requested_items = cluster_input.get("items") or []
        if not cluster_name or not requested_items:
            continue

        cluster_key = _normalize_cluster_key(cluster_name)
        matched_cluster = by_name.get(cluster_key)
        if not matched_cluster:
            for key, value in by_name.items():
                if cluster_key in key or key in cluster_key:
                    matched_cluster = value
                    break

        matched_name = str((matched_cluster or {}).get("name") or cluster_name)
        cluster_result: dict[str, Any] = {
            "cluster_name": cluster_name,
            "matched_name": matched_name,
            "items": [],
        }

        if not matched_cluster:
            cluster_result["status"] = "cluster_not_found"
            cluster_result["error"] = "Кластер не найден в таблице черновика"
            results.append(cluster_result)
            continue

        if not await _open_cluster_items_modal(page, matched_name):
            cluster_result["status"] = "modal_open_error"
            cluster_result["error"] = "Не удалось открыть модалку товаров"
            results.append(cluster_result)
            continue

        try:
            if not await _wait_for_cluster_items_modal(page):
                cluster_result["status"] = "modal_timeout"
                cluster_result["error"] = "Модалка товаров не открылась"
                results.append(cluster_result)
                continue

            await _activate_cluster_request_items_tab(page)
            cluster_changed = False

            for requested in requested_items:
                sku = str(requested.get("sku") or "").strip()
                requested_qty = int(requested.get("quantity") or 0)
                if not sku or requested_qty <= 0:
                    continue

                await _set_cluster_items_search(page, sku)
                summary = await _read_cluster_items_summary(page)
                accepted_qty = int(summary.get("unitsCount") or 0)
                removed_qty = max(0, requested_qty - accepted_qty)
                if accepted_qty != requested_qty:
                    cluster_changed = True
                    removed_total += removed_qty
                cluster_result["items"].append(
                    {
                        "sku": sku,
                        "requested_quantity": requested_qty,
                        "accepted_quantity": accepted_qty,
                        "removed_quantity": removed_qty,
                        "found_items_count": int(summary.get("itemsCount") or 0),
                    }
                )

            cluster_result["status"] = "ok"
            if cluster_changed:
                changed_clusters.append(matched_name)
            results.append(cluster_result)
        finally:
            await _close_modal(page)
            await page.wait_for_timeout(300)

    return {
        "clusters": results,
        "changed_clusters": changed_clusters,
        "removed_total": removed_total,
    }


async def _select_delivery_type(page, cluster_name: str = "") -> None:
    """Выбрать способ доставки в модалке.

    Москва → 'Привезу самостоятельно' (Direct)
    Остальные → 'Доставить кросс-докингом'
    """
    is_moscow = "москва" in cluster_name.lower() or "дальние" in cluster_name.lower()
    try:
        if is_moscow:
            # Кликаем radio "Привезу самостоятельно"
            direct = page.locator('[data-testid="DirectSelector"]')
            if await direct.count() > 0:
                await direct.click()
                await page.wait_for_timeout(500)
                return
        # Кросс-докинг для остальных
        label = page.get_by_text("Доставить кросс-докингом")
        if await label.count() > 0:
            await label.click()
            await page.wait_for_timeout(500)
    except Exception:
        pass


async def _type_warehouse_name(page, name: str) -> bool:
    """Ввести имя склада в поле 'Наименование или адрес'.

    Label перекрывает input (intercepts pointer events) —
    используем JS для фокуса и Playwright fill с force.
    """
    try:
        # Находим input через JS — ищем по label, по id-паттерну, по placeholder
        input_id = await page.evaluate("""
            () => {
                // 1) По label с текстом "Наименование" или "адрес"
                const labels = document.querySelectorAll('label');
                for (const l of labels) {
                    const t = l.textContent || '';
                    if ((t.includes('Наименование') || t.includes('адрес')) && l.getAttribute('for')) {
                        const inp = document.getElementById(l.getAttribute('for'));
                        if (inp && inp.offsetParent !== null) return l.getAttribute('for');
                    }
                }
                // 2) По id-паттерну baseInput___
                const byId = document.querySelectorAll('input[id^="baseInput"]');
                for (const inp of byId) {
                    if (inp.offsetParent !== null && inp.type !== 'checkbox' && inp.type !== 'radio') {
                        return inp.id;
                    }
                }
                // 3) Текстовый input внутри модалки (контейнер с "Точка отгрузки")
                const allInputs = document.querySelectorAll('input');
                for (const inp of allInputs) {
                    if (inp.offsetParent !== null && !inp.type && inp.className.includes('c8s80')) {
                        return inp.id || null;
                    }
                }
                return null;
            }
        """)
        if not input_id:
            _log("Поле ввода не найдено (ни по label, ни по id, ни по классу)")
            return False

        _log(f"    Найден input: #{input_id}")

        # Фокус → очистка → быстрая печать (keyboard.type вместо fill — триггерит React autocomplete)
        await page.evaluate(f"document.getElementById('{input_id}').focus()")
        await page.wait_for_timeout(100)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Delete")
        await page.wait_for_timeout(100)
        await page.keyboard.type(name, delay=5)
        await page.wait_for_timeout(800)  # ждём автокомплит

        # Кликаем первый результат в выпадающем списке (секция "Склады")
        dropdown_item = page.locator('[data-testid="WarehouseSearchEntryItem"]').first
        try:
            await dropdown_item.wait_for(timeout=3000)
            await dropdown_item.click()
            await page.wait_for_timeout(500)  # ждём загрузку карточки
        except Exception:
            _log(f"    Выпадающий список не появился")
            return False
        return True
    except Exception as e:
        _log(f"Ошибка ввода '{name}': {e}")
        return False


async def _read_card_timeslot(page) -> dict[str, Any]:
    """Прочитать таймслот и имя из карточки склада (data-testid=WarehouseSelectionModal)."""
    result: dict[str, Any] = {"timeslot": None, "warehouse_name": None, "limits": None}
    try:
        data = await page.evaluate("""
            () => {
                const card = document.querySelector('[data-testid="WarehouseSelectionModal"]');
                if (!card) return null;

                // Имя склада: div.heading-200
                const nameEl = card.querySelector('.heading-200');
                const name = nameEl?.textContent?.trim() || null;

                // Таймслот: div.heading-100
                const tsEl = card.querySelector('.heading-100');
                const timeslot = tsEl?.textContent?.trim() || null;

                // Лимиты: текст "Не более..."
                let limits = null;
                for (const el of card.querySelectorAll('div')) {
                    const t = el.textContent?.trim() || '';
                    if (t.startsWith('Не более') && t.length < 60 && el.children.length === 0) {
                        limits = t;
                        break;
                    }
                }

                // Кнопка disabled?
                const selectBtn = card.querySelector('button[data-testid="WarehouseSelectButton"]');
                const buttonDisabled = selectBtn?.disabled || false;

                return { name, timeslot, limits, buttonDisabled };
            }
        """)
        if data:
            result["warehouse_name"] = data.get("name")
            result["timeslot"] = data.get("timeslot")
            result["limits"] = data.get("limits")
            result["buttonDisabled"] = data.get("buttonDisabled", False)
    except Exception:
        pass
    return result


async def _scan_warehouse_in_modal(page, warehouse_name: str) -> dict[str, Any]:
    """В открытом модальном окне: ввести имя склада, прочитать таймслоты из обоих табов."""
    wh_result: dict[str, Any] = {
        "warehouse": warehouse_name,
        "found": False,
        "boxes": None,
        "pallets_and_boxes": None,
    }

    # Ввести имя
    if not await _type_warehouse_name(page, warehouse_name):
        wh_result["error"] = "Не удалось ввести имя"
        return wh_result

    # Проверяем появилась ли карточка
    card_data = await _read_card_timeslot(page)
    if not card_data.get("warehouse_name"):
        wh_result["found"] = False
        return wh_result

    wh_result["found"] = True

    # Читаем карточку из блока WarehouseSelectionModal
    card = await _read_card_timeslot(page)
    wh_result["card"] = card

    return wh_result


async def _sync_checkboxes(page, all_clusters: list[dict], expected_clusters: list[str]) -> list[str]:
    """
    Сверить чекбоксы в черновике с ожидаемыми кластерами.
    Снять галочки с кластеров, которых нет в expected_clusters.
    Поставить галочки на кластерах, которые есть в expected_clusters но не отмечены.

    Returns:
        Список снятых/поставленных кластеров для лога.
    """
    changes: list[str] = []
    expected_lower = {e.lower().strip() for e in expected_clusters}

    for c in all_clusters:
        name = c["name"]
        name_lower = name.lower().strip()
        is_checked = c["checked"]
        row_idx = c["rowIndex"]

        # Проверяем принадлежность (частичное совпадение)
        in_expected = any(
            exp in name_lower or name_lower in exp
            for exp in expected_lower
        )

        if is_checked and not in_expected:
            # Снять галочку — кластера нет в нашем отчёте
            _log(f"  Снимаю галочку: {name} (нет в отчёте)")
            try:
                await page.evaluate(f"""
                    () => {{
                        const rows = document.querySelectorAll('table tr');
                        const tds = rows[{row_idx}]?.querySelectorAll('td');
                        const cb = tds?.[0]?.querySelector('input[type="checkbox"]');
                        if (cb && cb.checked) cb.click();
                    }}
                """)
                await page.wait_for_timeout(300)
                changes.append(f"снял: {name}")
            except Exception as e:
                _log(f"  Ошибка снятия галочки {name}: {e}")

        elif not is_checked and in_expected:
            # Поставить галочку — кластер есть в отчёте но не отмечен
            _log(f"  Ставлю галочку: {name} (есть в отчёте)")
            try:
                await page.evaluate(f"""
                    () => {{
                        const rows = document.querySelectorAll('table tr');
                        const tds = rows[{row_idx}]?.querySelectorAll('td');
                        const cb = tds?.[0]?.querySelector('input[type="checkbox"]');
                        if (cb && !cb.checked) cb.click();
                    }}
                """)
                await page.wait_for_timeout(300)
                changes.append(f"поставил: {name}")
            except Exception as e:
                _log(f"  Ошибка установки галочки {name}: {e}")

    return changes


async def scan_warehouses_for_draft(
    page,
    draft_id: str,
    warehouses: list[str] | None = None,
    expected_clusters: list[str] | None = None,
) -> dict[str, Any]:
    """
    Основная функция: сканирует склады для всех активных кластеров черновика.

    Args:
        expected_clusters: список имён кластеров из отчёта Поставки.
            Если указан — сверяет чекбоксы: лишние снимает, недостающие ставит.
    """
    wh_list = warehouses or MSK_WAREHOUSES_TO_SCAN

    # Переходим на страницу черновика
    url = f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}"
    _log(f"Открываю {url}")
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # Ждём появления таблицы (макс 15 сек)
    _log(f"Текущий URL: {page.url}")

    try:
        await page.locator("table tr td").first.wait_for(timeout=15000)
        _log("Таблица загружена")
    except Exception:
        _log(f"Таблица не появилась. URL: {page.url}")
        from src.chrome_browser import wait_for_ozon_ready
        await wait_for_ozon_ready(page)
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)

    # Получаем кластеры из таблицы
    all_clusters = await _get_clusters_from_table(page)
    _log(f"Кластеров в таблице: {len(all_clusters)}")

    # Сверяем чекбоксы с ожидаемыми кластерами
    if expected_clusters:
        _log(f"Сверка с отчётом ({len(expected_clusters)} кластеров):")
        changes = await _sync_checkboxes(page, all_clusters, expected_clusters)
        if changes:
            _log(f"  Изменения: {', '.join(changes)}")
            # Перечитываем состояние после изменений
            all_clusters = await _get_clusters_from_table(page)
        else:
            _log("  Всё совпадает")

    active_clusters = [c for c in all_clusters if c["checked"]]
    _log(f"Активных (checked): {len(active_clusters)}")

    for c in all_clusters:
        mark = "+" if c["checked"] else " "
        _log(f"  [{mark}] {c['name']} — {c['buttonText']}")

    if not active_clusters:
        return {
            "draft_id": draft_id,
            "error": "Нет активных кластеров (отмеченных чекбоксами)",
            "all_clusters": [{"name": c["name"], "checked": c["checked"]} for c in all_clusters],
        }

    report: dict[str, Any] = {
        "draft_id": draft_id,
        "clusters": [],
    }

    for cluster in active_clusters:
        cluster_name = cluster["name"]
        row_idx = cluster["rowIndex"]
        _log(f"--- {cluster_name} (row {row_idx}) ---")

        cluster_result: dict[str, Any] = {
            "cluster": cluster_name,
            "warehouses_with_timeslots": [],
            "warehouses_no_timeslots": [],
            "errors": [],
        }

        # Нажимаем кнопку в последнем столбце (ищем по имени кластера + checked)
        _log(f"Кликаю кнопку: '{cluster['buttonText']}'")
        clicked = await _click_select_for_cluster(page, cluster_name)
        if not clicked:
            cluster_result["errors"].append("Не удалось кликнуть кнопку")
            report["clusters"].append(cluster_result)
            continue

        # Ждём модалку
        modal_opened = await _wait_for_modal(page)
        if not modal_opened:
            _log("Модалка не открылась, пробую ещё раз...")
            await _click_select_for_cluster(page, cluster_name)
            modal_opened = await _wait_for_modal(page, timeout=8000)

        if not modal_opened:
            cluster_result["errors"].append("Модальное окно не открылось после 2 попыток")
            report["clusters"].append(cluster_result)
            continue

        _log("Модалка открыта")

        # Выбираем кросс-докинг
        await _select_delivery_type(page, cluster_name)

        # Сканируем склады
        for wh_name in wh_list:
            _log(f"  → {wh_name}...")

            wh_data = await _scan_warehouse_in_modal(page, wh_name)

            card = wh_data.get("card") or {}
            ts_value = card.get("timeslot") or ""
            is_disabled = card.get("buttonDisabled", False)
            is_available = (
                ts_value
                and "недоступен" not in ts_value.lower()
                and not is_disabled
            )

            if is_available:
                cluster_result["warehouses_with_timeslots"].append({
                    "warehouse": wh_name,
                    "warehouse_name": card.get("warehouse_name"),
                    "timeslot": ts_value,
                    "limits": card.get("limits"),
                })
                _log(f"    OK {ts_value} | {card.get('limits', '')}")
            else:
                cluster_result["warehouses_no_timeslots"].append(wh_name)
                if is_disabled:
                    _log(f"    --кнопка disabled")
                elif "недоступен" in ts_value.lower():
                    _log(f"    --Недоступен")
                else:
                    _log(f"    --нет таймслота")

        # Закрываем модалку
        await _close_modal(page)
        await page.wait_for_timeout(1000)

        wh_count = len(cluster_result["warehouses_with_timeslots"])
        _log(f"Итог {cluster_name}: {wh_count} складов с таймслотами")

        report["clusters"].append(cluster_result)

    # Сохраняем лог
    log_path = Path("exports") / f"supply_scan_warehouses_{draft_id}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"Лог сохранён: {log_path}")

    return report


# ─── Выбор оптимального склада ────────────────────────────────────────────

def pick_optimal_warehouses(scan_result: dict[str, Any]) -> dict[str, str]:
    """
    Выбрать оптимальный склад для каждого кластера.

    Алгоритм:
    1. Найти склад общий для ВСЕХ кластеров (с таймслотом у всех)
    2. Если нет — группировать кластеры по общим складам
    3. Из общих выбрать приоритетный (первый в MSK_WAREHOUSES_TO_SCAN)

    Returns:
        {cluster_name: warehouse_name}
    """
    clusters = scan_result.get("clusters") or []
    if not clusters:
        return {}

    # Собираем множество складов с таймслотами для каждого кластера
    cluster_wh_sets: dict[str, set[str]] = {}
    for c in clusters:
        cname = c["cluster"]
        wh_names = set()
        for wh in c.get("warehouses_with_timeslots") or []:
            wh_names.add(wh["warehouse"])
        cluster_wh_sets[cname] = wh_names

    cluster_names = list(cluster_wh_sets.keys())

    # 1) Общий склад для ВСЕХ кластеров
    if len(cluster_names) > 1:
        common = cluster_wh_sets[cluster_names[0]]
        for cname in cluster_names[1:]:
            common = common & cluster_wh_sets[cname]

        if common:
            # Выбираем по приоритету (порядок в MSK_WAREHOUSES_TO_SCAN)
            for wh in MSK_WAREHOUSES_TO_SCAN:
                if wh in common:
                    _log(f"Общий склад для всех кластеров: {wh}")
                    return {cname: wh for cname in cluster_names}
            # Если ни один приоритетный не подошёл — берём первый из common
            best = next(iter(common))
            _log(f"Общий склад (не приоритетный): {best}")
            return {cname: best for cname in cluster_names}

    # 2) Нет общего → для каждого кластера берём лучший по приоритету
    result: dict[str, str] = {}
    for cname in cluster_names:
        available = cluster_wh_sets[cname]
        chosen = None
        for wh in MSK_WAREHOUSES_TO_SCAN:
            if wh in available:
                chosen = wh
                break
        if not chosen and available:
            chosen = next(iter(available))
        if chosen:
            result[cname] = chosen
            _log(f"  {cname} → {chosen}")
        else:
            _log(f"  {cname} → нет доступных складов")

    return result


# ─── Установка склада через UI ────────────────────────────────────────────

async def set_warehouses_for_draft(
    page,
    draft_id: str,
    warehouse_map: dict[str, str],
) -> dict[str, Any]:
    """
    Для каждого кластера: открыть модалку, ввести склад, нажать
    "Выбрать точку"/"Заменить точку", затем "Сохранить".

    Args:
        page: Playwright page
        draft_id: ID черновика
        warehouse_map: {cluster_name: warehouse_name}

    Returns:
        dict с результатами
    """
    url = f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}"
    _log(f"Открываю {url}")
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    try:
        await page.locator("table tr td").first.wait_for(timeout=15000)
    except Exception:
        from src.chrome_browser import wait_for_ozon_ready
        await wait_for_ozon_ready(page)
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)

    results: list[dict[str, Any]] = []

    for cluster_name, wh_name in warehouse_map.items():
        _log(f"--- {cluster_name} → {wh_name} ---")

        res: dict[str, Any] = {
            "cluster": cluster_name,
            "warehouse": wh_name,
            "status": "pending",
        }

        # Кликаем кнопку кластера (Выбрать или уже выбранный склад)
        clicked = await _click_select_for_cluster(page, cluster_name)
        if not clicked:
            res["status"] = "error"
            res["error"] = "Кнопка кластера не найдена"
            results.append(res)
            continue

        modal_opened = await _wait_for_modal(page)
        if not modal_opened:
            await _click_select_for_cluster(page, cluster_name)
            modal_opened = await _wait_for_modal(page, timeout=8000)
        if not modal_opened:
            res["status"] = "error"
            res["error"] = "Модалка не открылась"
            results.append(res)
            continue

        # Выбираем кросс-докинг
        await _select_delivery_type(page, cluster_name)

        # Вводим имя склада
        typed = await _type_warehouse_name(page, wh_name)
        if not typed:
            res["status"] = "error"
            res["error"] = "Не удалось ввести имя склада"
            await _close_modal(page)
            results.append(res)
            continue

        # Нажимаем "Выбрать точку" или "Заменить точку" через JS
        try:
            clicked_point = await page.evaluate("""
                () => {
                    // Ищем кнопку по data-testid или тексту
                    const selectBtns = document.querySelectorAll('button[data-testid="WarehouseSelectButton"], button');
                    for (const b of selectBtns) {
                        const t = b.textContent?.trim() || '';
                        if (t === 'Выбрать точку' || t === 'Заменить точку') {
                            // Проверяем disabled
                            if (b.disabled) return 'DISABLED:' + t;
                            b.click();
                            return t;
                        }
                    }
                    // Fallback: ищем span внутри div
                    const spans = document.querySelectorAll('span');
                    for (const s of spans) {
                        const t = s.textContent?.trim() || '';
                        if (t === 'Выбрать точку' || t === 'Заменить точку') {
                            const btn = s.closest('button');
                            if (btn?.disabled) return 'DISABLED:' + t;
                            s.closest('div[class*="c9r80"]')?.click() || s.parentElement?.click();
                            return t;
                        }
                    }
                    return null;
                }
            """)
            if clicked_point and clicked_point.startswith("DISABLED:"):
                res["status"] = "error"
                res["error"] = f"Кнопка '{clicked_point[9:]}' неактивна (склад недоступен)"
                _log(f"  Кнопка disabled — склад недоступен!")
                await _close_modal(page)
                results.append(res)
                continue
            elif clicked_point:
                _log(f"  Нажал '{clicked_point}'")
                await page.wait_for_timeout(500)
            else:
                res["status"] = "error"
                res["error"] = "Кнопка 'Выбрать/Заменить точку' не найдена"
                await _close_modal(page)
                results.append(res)
                continue
        except Exception as e:
            res["status"] = "error"
            res["error"] = f"Ошибка клика по кнопке точки: {e}"
            await _close_modal(page)
            results.append(res)
            continue

        # Нажимаем "Сохранить" через JS
        try:
            saved = await page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const b of buttons) {
                        if (b.textContent?.trim() === 'Сохранить' && b.offsetParent !== null) {
                            b.click();
                            return true;
                        }
                    }
                    return false;
                }
            """)
            if saved:
                _log("  Нажал 'Сохранить'")
                await page.wait_for_timeout(1000)
                res["status"] = "ok"
            else:
                res["status"] = "error"
                res["error"] = "Кнопка 'Сохранить' не найдена"
                await _close_modal(page)
        except Exception as e:
            res["status"] = "error"
            res["error"] = f"Ошибка при сохранении: {e}"
            await _close_modal(page)

        results.append(res)

    ok = sum(1 for r in results if r["status"] == "ok")
    _log(f"Итог: {ok}/{len(results)} складов установлено")

    return {
        "draft_id": draft_id,
        "ok": ok,
        "total": len(results),
        "results": results,
    }


# ─── Сбор таймслотов: этап 2 "Склад размещения" ───────────────────────────
#
#  На этапе 2 кластеры — аккордеоны.  Внутри каждого — одна или несколько
#  строк складов (storage warehouses), каждая с кнопкой "Выбрать" таймслот
#  и столбцом "Товары / Количество" (сколько товаров примет склад).
#  Без перезагрузок — просто раскрываем и кликаем.


def _to_msk(iso_utc: str | None) -> str:
    """Конвертировать ISO UTC строку в МСК строку."""
    if not iso_utc:
        return "-"
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(MSK)
    return dt.strftime("%d.%m.%Y %H:%M")


async def _wait_for_stage2_table(page, timeout: int = 60000) -> bool:
    """Дождаться таблицы этапа 2 'Склад размещения'.

    Ozon может долго считать доступность складов (до 60 сек).
    """
    try:
        # Ждём пока появится хотя бы одна строка таблицы
        await page.locator("table tr td").first.wait_for(timeout=timeout)
        await page.wait_for_timeout(1000)
        return True
    except Exception:
        _log(f"Таблица не загрузилась за {timeout/1000}с (Ozon считает доступность?)")
        return False


async def _expand_all_clusters(page) -> int:
    """Раскрыть все свёрнутые аккордеоны кластеров (шевроны ▸)."""
    expanded = await page.evaluate("""
        () => {
            let count = 0;
            const rows = document.querySelectorAll('table tr');
            for (const row of rows) {
                const tds = row.querySelectorAll('td');
                if (tds.length < 3) continue;
                const nameText = tds[1]?.textContent || '';
                // Строка-заголовок кластера содержит "N склад"
                if (!/\\d+\\s*склад/i.test(nameText)) continue;
                // Проверяем: есть шеврон (svg) и кластер свёрнут
                const chevron = tds[0]?.querySelector('svg')?.closest('div');
                if (chevron) {
                    chevron.click();
                    count++;
                }
            }
            return count;
        }
    """)
    if expanded:
        await page.wait_for_timeout(1000)
    return expanded or 0


async def _read_warehouse_rows(page) -> list[dict[str, Any]]:
    """Прочитать все строки складов из таблицы этапа 2.

    Возвращает список:
    [{cluster, warehouse, items_text, items_count, volume_liters,
      accepts_all, nearest_date, has_timeslot_button, row_index}]
    """
    data = await page.evaluate("""
        () => {
            const rows = document.querySelectorAll('table tr');
            const result = [];
            let currentCluster = '';

            for (let i = 0; i < rows.length; i++) {
                const tds = rows[i].querySelectorAll('td');
                if (tds.length < 3) continue;
                const nameText = tds[1]?.textContent?.trim() || '';

                // Строка-заголовок кластера: содержит "N склад(ов/а)"
                if (/\\d+\\s*склад/i.test(nameText)) {
                    // Извлекаем имя кластера (до "N склад")
                    currentCluster = nameText.replace(/\\d+\\s*склад.*$/i, '').trim();
                    continue;
                }

                // Строка склада: имя + "Примет все товары" или warning
                // Проверяем: есть radio input или кнопка "Выбрать"
                const hasRadio = !!rows[i].querySelector('input[type="radio"]');
                const hasButton = !!rows[i].querySelector('[data-testid="OpenTimeslotButton"]')
                    || Array.from(rows[i].querySelectorAll('button'))
                        .some(b => b.textContent?.trim() === 'Выбрать');

                if (!hasRadio && !hasButton) continue;
                if (!currentCluster) continue;

                // Имя склада (убираем "Примет все товары" и лишний текст)
                const warehouseName = nameText
                    .replace(/Примет все товары/g, '')
                    .replace(/Не все товары/g, '')
                    .split('\\n')[0]?.trim() || '';
                const acceptsAll = nameText.includes('Примет все товары');

                // Товары/Количество — ищем текст вида "N шт, N л"
                let itemsText = '';
                let itemsCount = 0;
                let volumeLiters = 0;
                for (const td of tds) {
                    const t = td.textContent?.trim() || '';
                    const m = t.match(/(\\d+)\\s*шт[.,]?\\s*(\\d+)\\s*л/);
                    if (m) {
                        itemsText = t;
                        itemsCount = parseInt(m[1]);
                        volumeLiters = parseInt(m[2]);
                        break;
                    }
                }

                // Ближайшая дата: ищем текст вида "31 марта" или "1 апреля"
                let nearestDate = '';
                for (const td of tds) {
                    const t = td.textContent?.trim() || '';
                    if (/\\d+\\s*(марта|апреля|мая|июня|июля)/i.test(t) && t.length < 30) {
                        nearestDate = t;
                        break;
                    }
                }

                // Помечаем кнопку "Выбрать" для клика
                const btn = rows[i].querySelector('[data-testid="OpenTimeslotButton"] button')
                    || rows[i].querySelector('[data-testid="OpenTimeslotButton"]')
                    || Array.from(rows[i].querySelectorAll('button'))
                        .find(b => b.textContent?.trim() === 'Выбрать');
                if (btn) {
                    btn.setAttribute('data-ts-row', String(result.length));
                }

                result.push({
                    cluster: currentCluster,
                    warehouse: warehouseName,
                    itemsText: itemsText,
                    itemsCount: itemsCount,
                    volumeLiters: volumeLiters,
                    acceptsAll: acceptsAll,
                    nearestDate: nearestDate,
                    hasButton: !!btn,
                    rowIndex: i,
                });
            }
            return result;
        }
    """)
    return data or []


async def _get_cluster_names_stage2(page) -> list[str]:
    """Получить имена кластеров из таблицы этапа 2."""
    data = await page.evaluate("""
        () => {
            const names = [];
            const rows = document.querySelectorAll('table tr');
            for (const row of rows) {
                const tds = row.querySelectorAll('td');
                if (tds.length < 3) continue;
                const nameText = tds[1]?.textContent?.trim() || '';
                if (/\\d+\\s*склад/i.test(nameText)) {
                    names.push(nameText.replace(/\\d+\\s*склад.*$/i, '').trim());
                }
            }
            return names;
        }
    """)
    return data or []


async def _open_delivery_modal(page, cluster_name: str) -> bool:
    """Открыть модалку 'Доставит Ozon' для кластера."""
    await _expand_all_clusters(page)
    await page.wait_for_timeout(300)

    found = await page.evaluate("""
        (clusterName) => {
            const rows = document.querySelectorAll('table tr');
            for (const row of rows) {
                const tds = row.querySelectorAll('td');
                if (tds.length < 4) continue;
                if (!tds[1]?.textContent?.includes(clusterName)) continue;
                for (const td of tds) {
                    if (td.textContent?.includes('Доставит Ozon')) {
                        const target = td.querySelector('div') || td;
                        target.setAttribute('data-delivery-click', 'true');
                        return true;
                    }
                }
            }
            return false;
        }
    """, cluster_name)
    if not found:
        return False

    try:
        target = page.locator("[data-delivery-click='true']").first
        await target.scroll_into_view_if_needed()
        await target.click()
        await page.evaluate("() => { const e = document.querySelector('[data-delivery-click]'); if (e) e.removeAttribute('data-delivery-click'); }")
    except Exception:
        return False

    modal = await _wait_for_modal(page)
    if modal:
        await _select_delivery_type(page, cluster_name)
    return modal


async def _check_warehouse_button(page) -> str:
    """Проверить состояние кнопки 'Выбрать/Заменить точку'.
    Returns: 'active' | 'disabled' | 'not_found'
    """
    return await page.evaluate("""
        () => {
            const btn = document.querySelector('button[data-testid="WarehouseSelectButton"]');
            if (btn && !btn.disabled) return 'active';
            if (btn?.disabled) return 'disabled';
            const all = document.querySelectorAll('button');
            for (const b of all) {
                const t = b.textContent?.trim() || '';
                if (t === 'Выбрать точку' || t === 'Заменить точку') {
                    return b.disabled ? 'disabled' : 'active';
                }
            }
            return 'not_found';
        }
    """) or "not_found"


async def _click_replace_and_save(page) -> bool:
    """Нажать 'Заменить точку' + 'Сохранить'. Returns True если успешно."""
    # Заменить точку
    clicked = await page.evaluate("""
        () => {
            const btn = document.querySelector('button[data-testid="WarehouseSelectButton"]');
            if (btn && !btn.disabled) { btn.click(); return true; }
            const all = document.querySelectorAll('button');
            for (const b of all) {
                const t = b.textContent?.trim() || '';
                if ((t === 'Выбрать точку' || t === 'Заменить точку') && !b.disabled) { b.click(); return true; }
            }
            return false;
        }
    """)
    if not clicked:
        return False
    await page.wait_for_timeout(500)

    # Сохранить
    saved = await page.evaluate("""
        () => {
            const save = document.querySelector('button[data-testid="SaveClusterDeliveryButton"]');
            if (save && save.offsetParent !== null) { save.click(); return true; }
            const all = document.querySelectorAll('button');
            for (const b of all) {
                if (b.textContent?.trim() === 'Сохранить' && b.offsetParent !== null) { b.click(); return true; }
            }
            return false;
        }
    """)
    if saved:
        await page.wait_for_timeout(3000)
    return saved


async def _collect_timeslots_current_state(
    page, only_cluster: str | None = None,
) -> list[dict[str, Any]]:
    """Раскрыть все кластеры, прочитать склады, кликнуть 'Выбрать' и собрать таймслоты.

    Args:
        only_cluster: если указан — собирать слоты только для этого кластера,
                      остальные пропускать. Сравнение без учёта регистра.
    """
    await _expand_all_clusters(page)
    await page.wait_for_timeout(500)

    wh_rows = await _read_warehouse_rows(page)
    results: list[dict[str, Any]] = []

    only_key = only_cluster.strip().lower() if only_cluster else None

    for idx, wh in enumerate(wh_rows):
        if not wh["hasButton"]:
            continue

        # Пропускаем кластеры, которые не запрашивали
        if only_key:
            row_cluster = (wh.get("cluster") or "").strip().lower()
            if only_key not in row_cluster and row_cluster not in only_key:
                continue

        _log(f"    >> {wh['warehouse']} ({wh['itemsCount']}шт)...")
        ts_data: dict[str, Any] | None = None
        try:
            async with page.expect_response(
                lambda r: "get-timeslots" in r.url and r.status == 200,
                timeout=15000,
            ) as response_info:
                btn = page.locator(f'[data-ts-row="{idx}"]')
                await btn.scroll_into_view_if_needed()
                await btn.click()
            response = await response_info.value
            ts_data = await response.json()
        except Exception as e:
            _log(f"      Ошибка: {e}")

        if ts_data:
            slots = ts_data.get("timeslots") or []
            first_str = _to_msk((slots[0] if slots else {}).get("fromUtc"))
            last_str = _to_msk((slots[-1] if slots else {}).get("fromUtc"))
            _log(f"      {len(slots)} слотов | {first_str} ... {last_str}")
            results.append({
                **wh,
                "timezone": ts_data.get("timezone") or {},
                "slotsCount": len(slots),
                "slots": slots,
                "first": slots[0] if slots else None,
                "last": slots[-1] if slots else None,
            })
        else:
            results.append({**wh, "error": "get-timeslots не перехвачен", "slotsCount": 0, "slots": []})

        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)

    return results


async def collect_timeslots_for_draft(
    page, draft_id: str, scan_result: dict[str, Any] | None = None,
    transit_warehouses: list[str] | None = None,
) -> dict[str, Any]:
    """
    Сбор таймслотов на этапе 2 "Склад размещения".

    Для каждого кластера:
      1. Открыть модалку "Доставит Ozon" ОДИН РАЗ
      2. Перебирать транзитные склады прямо в поле ввода (без закрытия модалки)
      3. Если кнопка disabled → следующий склад (не закрывая модалку)
      4. Если кнопка активна → "Заменить точку" → "Сохранить" → собрать таймслоты
      5. Вернуться к модалке для следующего транзитного
    """
    url = f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}"
    wh_list = transit_warehouses or MSK_WAREHOUSES_TO_SCAN

    # Убеждаемся что мы на странице черновика
    if "multi-cluster" not in page.url or draft_id not in page.url:
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

    if not await _wait_for_stage2_table(page):
        from src.chrome_browser import wait_for_ozon_ready
        await wait_for_ozon_ready(page)
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)

    cluster_names = await _get_cluster_names_stage2(page)
    _log(f"Кластеров: {len(cluster_names)}: {cluster_names}")

    if not cluster_names:
        return {"error": "Кластеры не найдены", "draft_id": draft_id,
                "timeslots": [], "total_warehouses": 0, "total_slots": 0}

    all_timeslots: list[dict[str, Any]] = []
    total_slots = 0

    for cluster_name in cluster_names:
        _log(f"")
        _log(f"=== Кластер: {cluster_name} ===")
        is_moscow = "москва" in cluster_name.lower() or "дальние" in cluster_name.lower()

        # Москва (Direct) — не перебираем транзитные, сразу собираем слоты
        if is_moscow:
            _log(f"  Москва (Direct) — собираю таймслоты текущего состояния...")
            if "multi-cluster" not in page.url or draft_id not in page.url:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                await _wait_for_stage2_table(page)
            ts_rows = await _collect_timeslots_current_state(page, only_cluster=cluster_name)
            for row in ts_rows:
                row["transitWarehouse"] = "direct"
                total_slots += row.get("slotsCount", 0)
                all_timeslots.append(row)
            continue

        wh_idx = 0
        while wh_idx < len(wh_list):
            # Убеждаемся что мы на странице черновика
            if "multi-cluster" not in page.url or draft_id not in page.url:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                await _wait_for_stage2_table(page)

            # Открываем модалку
            _log(f"  Открываю модалку для {cluster_name}...")
            modal = await _open_delivery_modal(page, cluster_name)
            if not modal:
                _log(f"  Модалка не открылась, пропускаю кластер")
                break

            # Перебираем транзитные склады БЕЗ закрытия модалки
            while wh_idx < len(wh_list):
                tw = wh_list[wh_idx]
                _log(f"  Пробую {tw}...")

                typed = await _type_warehouse_name(page, tw)
                if not typed:
                    _log(f"    Не удалось ввести")
                    wh_idx += 1
                    continue

                btn_state = await _check_warehouse_button(page)
                if btn_state == "disabled":
                    _log(f"    Disabled — пропуск")
                    wh_idx += 1
                    continue
                if btn_state == "not_found":
                    _log(f"    Кнопка не найдена — пропуск")
                    wh_idx += 1
                    continue

                # Кнопка активна — жмём "Заменить точку" + "Сохранить"
                _log(f"    Активна! Сохраняю {tw}...")
                ok = await _click_replace_and_save(page)
                wh_idx += 1

                if not ok:
                    _log(f"    Ошибка при сохранении")
                    break  # модалка закрылась, нужно переоткрыть

                _log(f"    Сохранено. Собираю таймслоты для {cluster_name}...")

                # После "Сохранить" страница перезагрузилась — модалка закрыта
                await _wait_for_stage2_table(page)

                # Собираем таймслоты только для текущего кластера
                ts_rows = await _collect_timeslots_current_state(page, only_cluster=cluster_name)
                for row in ts_rows:
                    row["transitWarehouse"] = tw
                    total_slots += row.get("slotsCount", 0)
                all_timeslots.extend(ts_rows)

                break  # Модалка закрылась после сохранения, нужно переоткрыть для следующего

    result = {
        "draft_id": draft_id,
        "timeslots": all_timeslots,
        "total_warehouses": len(all_timeslots),
        "total_slots": total_slots,
    }

    out_path = Path("exports") / f"supply_timeslots_{draft_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"Таймслоты сохранены: {out_path}")

    return result


# ─── Фильтрация таймслотов по дате отгрузки ───────────────────────────────

MSK = timezone(timedelta(hours=3))


def _slot_date_msk(slot: dict[str, Any]) -> datetime:
    """Получить дату/время слота в МСК."""
    return datetime.fromisoformat(
        slot["fromUtc"].replace("Z", "+00:00")
    ).astimezone(MSK)


def filter_timeslots(
    timeslots_data: dict[str, Any],
    target_date: str | None = None,
) -> dict[str, Any]:
    """
    Фильтрация собранных таймслотов — выбор лучшего склада для каждого кластера.

    Логика:
      - Если target_date пуст → целевая дата = сегодня + 10 дней
      - Если target_date указана → ищем слоты на эту дату
      - Если для кластера нет слотов на целевую дату → расширяем ±1, ±2, ±3 дня
      - Из найденных слотов берём ближайший к целевой дате
      - Для каждого кластера выбираем склад с наибольшим количеством слотов
        на целевую дату (больше выбор = удобнее)

    Args:
        timeslots_data: результат collect_timeslots_for_draft (dict с ключом 'timeslots')
        target_date: дата отгрузки "YYYY-MM-DD" или None

    Returns:
        dict с результатами фильтрации по кластерам
    """
    if target_date:
        target = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=MSK)
    else:
        target = datetime.now(MSK) + timedelta(days=2)

    target_day = target.date()
    _log(f"Целевая дата отгрузки: {target_day}")

    all_ts = timeslots_data.get("timeslots") or []

    # Группируем по кластеру
    by_cluster: dict[str, list[dict[str, Any]]] = {}
    for entry in all_ts:
        if entry.get("error") or not entry.get("slots"):
            continue
        cluster = entry["cluster"]
        by_cluster.setdefault(cluster, []).append(entry)

    results: dict[str, Any] = {
        "target_date": str(target_day),
        "clusters": {},
    }

    for cluster_name, entries in by_cluster.items():
        _log(f"--- {cluster_name} ---")

        best_warehouse: dict[str, Any] | None = None

        # Пробуем целевую дату, потом ±1, ±2, ±3
        for delta in range(4):  # 0, 1, 2, 3
            if best_warehouse:
                break

            # При delta=0 — только target_day
            # При delta>0 — два дня: target ± delta
            check_days = [target_day] if delta == 0 else [
                target_day + timedelta(days=delta),
                target_day - timedelta(days=delta),
            ]

            for check_day in check_days:
                if best_warehouse:
                    break

                candidates: list[dict[str, Any]] = []

                for entry in entries:
                    wh_name = entry["warehouse"]
                    # Фильтруем слоты на check_day
                    day_slots = [
                        s for s in entry["slots"]
                        if _slot_date_msk(s).date() == check_day
                    ]
                    if not day_slots:
                        continue

                    # Ближайший слот к началу дня (утренний предпочтительнее)
                    earliest = min(day_slots, key=lambda s: s["fromUtc"])

                    candidates.append({
                        "warehouse": wh_name,
                        "transitWarehouse": entry.get("transitWarehouse", ""),
                        "date": str(check_day),
                        "slots_on_date": len(day_slots),
                        "earliest_slot": earliest,
                        "earliest_msk": _to_msk(earliest["fromUtc"]),
                        "total_slots": entry["slotsCount"],
                        "itemsCount": entry.get("itemsCount", 0),
                        "volumeLiters": entry.get("volumeLiters", 0),
                        "acceptsAll": entry.get("acceptsAll", False),
                    })

                if candidates:
                    # Приоритет: 1) больше товаров, 2) больше слотов
                    candidates.sort(
                        key=lambda c: (c["itemsCount"], c["slots_on_date"]),
                        reverse=True,
                    )
                    best_warehouse = candidates[0]
                    best_warehouse["delta_days"] = delta
                    best_warehouse["all_candidates"] = candidates[1:]  # без best (избегаем circular ref)

                    delta_str = "" if delta == 0 else f" (±{delta} дн.)"
                    _log(f"  {best_warehouse['warehouse']} | {check_day}{delta_str} | "
                         f"{best_warehouse['slots_on_date']} слотов | "
                         f"ближайший: {best_warehouse['earliest_msk']}")

        if best_warehouse:
            results["clusters"][cluster_name] = best_warehouse
        else:
            _log(f"  Нет слотов в диапазоне ±3 дня от {target_day}")
            results["clusters"][cluster_name] = {
                "error": f"Нет слотов ±3 дня от {target_day}",
            }

    return results


# ─── Полный флоу: скан + выбор + установка ────────────────────────────────

async def scan_and_set_warehouses(
    page,
    draft_id: str,
    warehouses: list[str] | None = None,
    expected_clusters: list[str] | None = None,
    requested_clusters: list[dict[str, Any]] | None = None,
    collect_timeslots: bool = True,
) -> dict[str, Any]:
    """
    Полный флоу:
    1. Сверяет чекбоксы + ставит первый доступный склад для каждого кластера
    2. Нажимает "Далее" → этап 2 "Склад размещения"
    3. На этапе 2 собирает таймслоты для всех складов (Ozon сам показывает доступные)
    """
    wh_list = warehouses or MSK_WAREHOUSES_TO_SCAN
    result: dict[str, Any] = {"draft_id": draft_id}

    url = f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}"
    _log(f"Открываю {url}")
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    try:
        await page.locator("table tr td").first.wait_for(timeout=15000)
        _log("Таблица загружена")
    except Exception:
        from src.chrome_browser import wait_for_ozon_ready
        await wait_for_ozon_ready(page)
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)

    # Получаем кластеры
    all_clusters = await _get_clusters_from_table(page)
    _log(f"Кластеров: {len(all_clusters)}")

    # Сверяем чекбоксы
    if expected_clusters:
        changes = await _sync_checkboxes(page, all_clusters, expected_clusters)
        if changes:
            _log(f"Чекбоксы: {', '.join(changes)}")
            all_clusters = await _get_clusters_from_table(page)

    active_clusters = [c for c in all_clusters if c["checked"]]
    _log(f"Активных: {len(active_clusters)}")

    if not active_clusters:
        result["error"] = "Нет активных кластеров"
        return result

    # ШАГ 1: Для каждого кластера ставим первый доступный склад
    _log("=" * 50)
    _log("ШАГ 1: Установка первого доступного склада")
    _log("=" * 50)

    set_results: list[dict[str, Any]] = []
    for cluster in active_clusters:
        cluster_name = cluster["name"]
        _log(f"--- {cluster_name} ---")

        # Если уже выбран склад — пропускаем
        if not cluster.get("needsSelection"):
            _log(f"  Склад уже выбран: {cluster.get('buttonText', '?')}")
            set_results.append({"cluster": cluster_name, "status": "already_set"})
            continue

        # Кликаем кнопку → открываем модалку
        clicked = await _click_select_for_cluster(page, cluster_name)
        if not clicked:
            set_results.append({"cluster": cluster_name, "status": "error", "error": "Кнопка не найдена"})
            continue

        modal_opened = await _wait_for_modal(page)
        if not modal_opened:
            set_results.append({"cluster": cluster_name, "status": "error", "error": "Модалка не открылась"})
            continue

        is_moscow = "москва" in cluster_name.lower() or "дальние" in cluster_name.lower()
        await _select_delivery_type(page, cluster_name)

        # Москва (Direct) — не нужен транзитный склад, просто сохраняем
        if is_moscow:
            _log(f"  Москва → 'Привезу самостоятельно', сохраняю...")
            saved = await page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const b of buttons) {
                        if (b.textContent?.trim() === 'Сохранить' && b.offsetParent !== null) {
                            b.click(); return true;
                        }
                    }
                    return false;
                }
            """)
            if saved:
                await page.wait_for_timeout(2000)
                set_results.append({"cluster": cluster_name, "status": "ok", "warehouse": "direct"})
            else:
                await _close_modal(page)
                set_results.append({"cluster": cluster_name, "status": "error", "error": "Сохранить не найдена"})
            continue

        # Пробуем первый доступный склад из списка (кросс-докинг)
        set_ok = False
        for wh_name in wh_list:
            typed = await _type_warehouse_name(page, wh_name)
            if not typed:
                continue

            card = await _read_card_timeslot(page)
            if card.get("buttonDisabled") or not card.get("warehouse_name"):
                continue

            # Нашли доступный склад — нажимаем "Выбрать точку"
            btn_text = await page.evaluate("""
                () => {
                    const btn = document.querySelector('button[data-testid="WarehouseSelectButton"]');
                    if (btn && !btn.disabled) { btn.click(); return btn.textContent?.trim(); }
                    return null;
                }
            """)
            if btn_text:
                _log(f"  Установил: {wh_name} ({btn_text})")
                await page.wait_for_timeout(500)

                # "Сохранить"
                await page.evaluate("""
                    () => {
                        const buttons = document.querySelectorAll('button');
                        for (const b of buttons) {
                            if (b.textContent?.trim() === 'Сохранить' && b.offsetParent !== null) {
                                b.click(); return true;
                            }
                        }
                        return false;
                    }
                """)
                _log(f"  Сохранил")
                await page.wait_for_timeout(2000)
                set_results.append({"cluster": cluster_name, "status": "ok", "warehouse": wh_name})
                set_ok = True
                break

        if not set_ok:
            await _close_modal(page)
            set_results.append({"cluster": cluster_name, "status": "error", "error": "Нет доступных складов"})

    result["set_results"] = set_results
    ok_count = sum(1 for r in set_results if r["status"] in ("ok", "already_set"))
    _log(f"Установлено: {ok_count}/{len(set_results)}")

    # ШАГ 2: Нажимаем "Далее"
    if requested_clusters:
        _log("")
        _log("=" * 50)
        _log("РЁРђР“ 1.5: РЎРІРµСЂРєР° С„Р°РєС‚РёС‡РµСЃРєРёС… РєРѕР»РёС‡РµСЃС‚РІ РІ С‡РµСЂРЅРѕРІРёРєРµ")
        _log("=" * 50)
        try:
            reconciliation = await reconcile_cluster_quantities(page, requested_clusters)
            result["quantity_reconciliation"] = reconciliation
            _log(
                f"РЎРєРѕСЂСЂРµРєС‚РёСЂРѕРІР°РЅРѕ: {len(reconciliation.get('changed_clusters') or [])} "
                f"РєР»Р°СЃС‚РµСЂРѕРІ, СЃРЅСЏС‚Рѕ {int(reconciliation.get('removed_total') or 0)} С€С‚."
            )
        except Exception as e:
            _log(f"РћС€РёР±РєР° СЃРІРµСЂРєРё РєРѕР»РёС‡РµСЃС‚РІ: {e}")
            result["quantity_reconciliation"] = {"error": str(e), "clusters": []}

    if not collect_timeslots:
        _log("Сбор слотов отключён (collect_timeslots=False), пропускаю ШАГ 2 и ШАГ 3")
        result["next_clicked"] = False
        result["timeslots_skipped"] = True
    elif ok_count == len(set_results):
        _log("")
        _log("=" * 50)
        _log("ШАГ 2: Нажимаю 'Далее'")
        _log("=" * 50)

        # Перезагружаем страницу если были изменения (после "Сохранить" страница могла перезагрузиться)
        if any(r["status"] == "ok" for r in set_results):
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            try:
                await page.locator("table tr td").first.wait_for(timeout=15000)
            except Exception:
                pass

        try:
            clicked_next = await page.evaluate("""
                () => {
                    const btn = document.querySelector('button[data-testid="MultiDraftToCalculationButton"]');
                    if (btn && !btn.disabled) { btn.click(); return true; }
                    const buttons = document.querySelectorAll('button[type="submit"]');
                    for (const b of buttons) {
                        if (b.textContent?.trim() === 'Далее' && !b.disabled) { b.click(); return true; }
                    }
                    return false;
                }
            """)
            if clicked_next:
                _log("Нажал 'Далее'")
                await page.wait_for_timeout(3000)
                result["next_clicked"] = True
            else:
                _log("Кнопка 'Далее' не найдена или disabled")
                result["next_clicked"] = False
        except Exception as e:
            _log(f"Ошибка 'Далее': {e}")
            result["next_clicked"] = False
    else:
        _log(f"Пропускаю 'Далее' — установлено {ok_count}/{len(set_results)}")
        result["next_clicked"] = False

    # ШАГ 3: Сбор таймслотов на этапе 2
    if result.get("next_clicked"):
        _log("")
        _log("=" * 50)
        _log("ШАГ 3: Сбор таймслотов на этапе 2")
        _log("=" * 50)
        try:
            ts_result = await collect_timeslots_for_draft(page, draft_id, transit_warehouses=wh_list)
            result["timeslots"] = ts_result
            _log(f"Итого: {ts_result.get('total_slots', 0)} слотов "
                 f"для {ts_result.get('total_warehouses', 0)} складов")
        except Exception as e:
            _log(f"Ошибка сбора таймслотов: {e}")
            result["timeslots"] = {"error": str(e)}

    # Сохраняем
    log_path = Path("exports") / f"supply_full_flow_{draft_id}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"Результат сохранён: {log_path}")

    return result


async def run_standalone(draft_id: str, scan_only: bool = False) -> None:
    """Запуск как standalone скрипт."""
    from src.chrome_browser import OzonBrowser

    async with OzonBrowser() as page:
        if scan_only:
            result = await scan_warehouses_for_draft(page, draft_id)
        else:
            result = await scan_and_set_warehouses(page, draft_id)

    print("\n" + "=" * 60)
    print("  РЕЗУЛЬТАТ")
    print("=" * 60)

    # Выбранные склады
    wh_map = result.get("warehouse_selection") or {}
    if isinstance(wh_map, dict) and not wh_map.get("error"):
        print("\n  Выбранные склады:")
        for cname, wh in wh_map.items():
            print(f"    {cname} → {wh}")

    # Результат установки
    set_res = result.get("warehouse_set_result") or {}
    if set_res:
        ok = set_res.get("ok", 0)
        total = set_res.get("total", 0)
        print(f"\n  Установлено: {ok}/{total}")
        for r in set_res.get("results", []):
            icon = "OK" if r["status"] == "ok" else "FAIL"
            print(f"    {icon} {r['cluster']} → {r['warehouse']} [{r['status']}]")
            if r.get("error"):
                print(f"       {r['error']}")


if __name__ == "__main__":
    import argparse
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--draft-id", required=True)
    p.add_argument("--scan-only", action="store_true", help="Только сканировать, не устанавливать")
    args = p.parse_args()
    asyncio.run(run_standalone(args.draft_id, scan_only=args.scan_only))
