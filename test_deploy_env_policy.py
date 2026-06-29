from pathlib import Path

from src.db_source_guard import validate_database_source


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_deploy_env_files_allow_declared_local_server_database() -> None:
    env_files = [
        Path("deploy/ozon-dashboard.env.example"),
        Path("dist/ozon-dashboard.env"),
    ]

    for env_file in env_files:
        if not env_file.exists():
            continue
        env = _read_env(env_file)

        validate_database_source(
            env["DATABASE_URL"],
            source_mode=env.get("DB_SOURCE_MODE", "server"),
            allow_local_database=env.get("ALLOW_LOCAL_DATABASE", "").lower() == "true",
            expected_host=env.get("EXPECTED_DB_HOST") or None,
        )
