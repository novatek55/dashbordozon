from src.dashboard.routes import system


def test_build_sync_status_response_normalizes_completed_progress_to_100():
    original = dict(system.sync_status)
    try:
        system.sync_status.update(
            {
                "is_running": False,
                "progress": 95,
                "stage": "Обновление отзывов",
                "current_detail": "Сохраняем данные в базу",
                "current_log": [],
                "stages": [
                    {"stage": "Обновление отзывов", "progress": 95},
                    {"stage": "Завершено", "progress": 100},
                ],
                "step_results": [],
                "started_at": "2026-06-14T17:00:00",
                "completed_at": "2026-06-14T17:05:00",
                "error": None,
            }
        )

        payload = system.build_sync_status_response()

        assert payload["progress"] == 100
        assert payload["stage"] == "Завершено"
    finally:
        system.sync_status.clear()
        system.sync_status.update(original)
