from src.dashboard.routes.unitka import normalize_offer_id


def matches_offer(item: dict, offer_id: str) -> bool:
    target_norm = normalize_offer_id(offer_id).lower()
    candidates = {
        normalize_offer_id(item.get("offer_id") or "").lower(),
        normalize_offer_id(item.get("offer_id_normalized") or "").lower(),
    }
    candidates.discard("")
    return target_norm in candidates


def test_accrual_match_uses_raw_offer_id_when_normalized_contains_sku():
    item = {
        "offer_id": "403 цинк",
        "offer_id_normalized": "sku:2021102960",
    }

    assert matches_offer(item, "403 цинк")


def test_accrual_match_still_supports_normalized_identity():
    item = {
        "offer_id": "",
        "offer_id_normalized": "sku:2021102960",
    }

    assert matches_offer(item, "sku:2021102960")
