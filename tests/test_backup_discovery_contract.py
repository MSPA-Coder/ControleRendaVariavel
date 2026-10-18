"""Backup identifica o servidor mesmo quando a imagem é trocada por digest."""

from pathlib import Path

import yaml


def test_only_database_server_declares_postgres_backup_role():
    services = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "compose.yaml").read_text(encoding="utf-8"),
    )["services"]
    assert services["db"]["labels"]["mspa.backup.postgres"] == "true"
    for name in ("web", "migrate", "db-provision"):
        assert "mspa.backup.postgres" not in services[name].get("labels", {})
