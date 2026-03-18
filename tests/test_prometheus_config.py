"""Regression tests for Prometheus multi-target scraping config."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def test_prometheus_uses_file_sd_for_agent_targets() -> None:
    with Path("config/prometheus.yml").open("r", encoding="utf-8") as file_handle:
        raw = yaml.safe_load(file_handle)

    scrape_configs = raw["scrape_configs"]
    trading_agent_job = next(
        (job for job in scrape_configs if job["job_name"] == "trading-agent"),
        None,
    )
    assert (
        trading_agent_job is not None
    ), "Prometheus config is missing scrape_config with job_name 'trading-agent'"

    assert "static_configs" not in trading_agent_job
    assert trading_agent_job["file_sd_configs"] == [
        {"files": ["/etc/prometheus/file_sd/agents.json"]}
    ]


def test_file_sd_targets_cover_all_current_agent_services() -> None:
    with Path("config/prometheus/agents.json").open("r", encoding="utf-8") as file_handle:
        entries = json.load(file_handle)

    labels_by_service = {entry["labels"]["service"]: entry["labels"] for entry in entries}
    targets_by_service = {entry["labels"]["service"]: entry["targets"] for entry in entries}

    assert set(labels_by_service) == {
        "agent",
        "agent_2",
        "agent_btc",
        "agent_sol_sparse",
        "agent_sentiment_macro",
    }
    assert targets_by_service["agent"] == ["agent:8000"]
    assert targets_by_service["agent_2"] == ["agent_2:8000"]
    assert targets_by_service["agent_btc"] == ["agent_btc:8000"]
    assert targets_by_service["agent_sol_sparse"] == ["agent_sol_sparse:8000"]
    assert targets_by_service["agent_sentiment_macro"] == ["agent_sentiment_macro:8000"]
    assert labels_by_service["agent"]["agent_id"] == "default"
    assert labels_by_service["agent_2"]["agent_id"] == "agent2"
    assert labels_by_service["agent_btc"]["agent_id"] == "btc-4h"
    assert labels_by_service["agent_sol_sparse"]["agent_id"] == "sol-trend-pullback-sparse"
    assert labels_by_service["agent_sentiment_macro"]["agent_id"] == "sentiment-macro-bot"


def test_compose_mounts_prometheus_target_directory() -> None:
    with Path("docker-compose.yml").open("r", encoding="utf-8") as file_handle:
        raw = yaml.safe_load(file_handle)

    prometheus_service = raw["services"]["prometheus"]

    assert "./config/prometheus:/etc/prometheus/file_sd:ro" in prometheus_service["volumes"]


def test_compose_does_not_enable_internal_prometheus_basic_auth() -> None:
    with Path("docker-compose.yml").open("r", encoding="utf-8") as file_handle:
        raw = yaml.safe_load(file_handle)

    prometheus_service = raw["services"]["prometheus"]

    assert "./config/auth:/etc/prometheus/auth:ro" not in prometheus_service["volumes"]
    assert "--web.config.file=/etc/prometheus/auth/web.yml" not in prometheus_service.get(
        "command", []
    )


def test_grafana_prometheus_datasource_uses_internal_network_without_basic_auth() -> None:
    with Path("config/grafana/provisioning/datasources/datasources.yml").open(
        "r",
        encoding="utf-8",
    ) as file_handle:
        raw = yaml.safe_load(file_handle)

    prometheus_datasource = next(
        datasource for datasource in raw["datasources"] if datasource["uid"] == "prometheus"
    )

    assert prometheus_datasource["url"] == "http://prometheus:9090"
    assert "basicAuth" not in prometheus_datasource
    assert "basicAuthUser" not in prometheus_datasource
    assert "secureJsonData" not in prometheus_datasource or (
        "basicAuthPassword" not in prometheus_datasource["secureJsonData"]
    )
