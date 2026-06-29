from src.dashboard.bestsellers import normalize_bestsellers_percent


def test_normalize_bestsellers_percent_accepts_fraction():
    assert normalize_bestsellers_percent(0.48) == 0.48


def test_normalize_bestsellers_percent_converts_whole_percent_to_fraction():
    assert normalize_bestsellers_percent(48) == 0.48
