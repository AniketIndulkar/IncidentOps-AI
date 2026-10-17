"""The deployment files against the settings (W13): what a newcomer sets in .env must take effect.

The clean deployment found settings the guide told people to set that docker-compose.yml never
passed to the API container. These tests keep the three lists (Settings, compose, .env.example)
from drifting apart again.
"""

import re
from collections import Counter
from pathlib import Path

import yaml

from incidentops_ai.config import Settings

ROOT = Path(__file__).resolve().parents[1]
# A deployment must never answer with a simulated model: these exist for the load tests (W12).
NOT_FOR_DEPLOYMENT = {"llm_provider", "simulated_latency_scale"}
INTERPOLATION = re.compile(r"^\$\{(?P<var>[A-Z0-9_]+):-(?P<default>.*)\}$")


def env_name(field: str) -> str:
    alias = Settings.model_fields[field].validation_alias
    return alias.choices[0] if alias is not None else f"INCIDENTOPS_{field.upper()}"


DEPLOYABLE = [env_name(field) for field in Settings.model_fields if field not in NOT_FOR_DEPLOYMENT]


def api_environment() -> dict[str, str]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    return compose["services"]["api"]["environment"]


def env_example_names() -> list[str]:
    lines = (ROOT / ".env.example").read_text().splitlines()
    return [line.split("=", 1)[0] for line in lines if re.match(r"^[A-Z0-9_]+=", line)]


def test_every_setting_reaches_the_api_container():
    environment = api_environment()

    assert [name for name in DEPLOYABLE if name not in environment] == []


def test_the_simulated_model_cannot_be_switched_on_in_a_deployment():
    environment = api_environment()

    assert {env_name(field) for field in NOT_FOR_DEPLOYMENT} & set(environment) == set()


def test_compose_keeps_no_second_copy_of_the_defaults():
    # A default written into compose would quietly override a default changed in config.py.
    copies = {}
    for name, value in api_environment().items():
        if name == "INCIDENTOPS_DATABASE_URL":  # compose points it at its own db service
            continue
        match = INTERPOLATION.match(str(value))
        assert match and match["var"] == name, f"{name}: {value}"
        if match["default"]:
            copies[name] = match["default"]

    assert copies == {}


def test_every_setting_is_listed_once_in_the_env_example():
    listed = Counter(env_example_names())

    assert [name for name in DEPLOYABLE if listed[name] != 1] == []


def test_blank_values_from_compose_mean_the_default(monkeypatch):
    # compose passes VAR="" for a variable .env does not set; a number must not fail to parse.
    for name in ("INCIDENTOPS_DATABASE_POOL_SIZE", "INCIDENTOPS_STALE_RUN_AFTER_S"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("INCIDENTOPS_LLM_ROUTES", "")

    settings = Settings(_env_file=None)

    assert settings.database_pool_size == 5
    assert settings.stale_run_after_s == 900
    assert settings.llm_routes == {}
