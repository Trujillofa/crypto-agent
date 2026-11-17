"""Guard against using unsupported wget options in the node-exporter image."""

from pathlib import Path

import yaml


def test_production_node_exporter_healthcheck_uses_wget_get():
    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "docker-compose.prod.yml").read_text()
    )
    service = compose["services"]["node_exporter"]
    assert service["image"] == "prom/node-exporter:v1.8.2"
    assert service["healthcheck"]["test"] == [
        "CMD-SHELL",
        "wget -q -O /dev/null http://localhost:9100/metrics || exit 1",
    ]
