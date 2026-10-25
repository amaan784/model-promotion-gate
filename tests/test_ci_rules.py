"""Static checks on the CI rules that decide when a branch pipeline trains."""

from __future__ import annotations

import yaml
from conftest import REPO


def _ci() -> dict:
    return yaml.safe_load(
        (REPO / ".gitlab-ci.yml").read_text(encoding="utf-8").replace("!reference", "")
    )


def test_branch_pipelines_train_only_when_the_model_can_change() -> None:
    ci = _ci()
    rules = ci[".trains_on_branch"]["rules"]
    changes = next(r["changes"] for r in rules if "changes" in r)
    assert set(changes["paths"]) == {"config/**/*", "modelgate/**/*", "requirements.txt"}
    assert changes["compare_to"] == "refs/heads/main"
    assert any("[train" in r.get("if", "") for r in rules)  # the manual override
    for job in ("train_evaluate", "register_challenger"):
        assert [".trains_on_branch", "rules"] in ci[job]["rules"]


def test_promotion_needs_a_champion_file() -> None:
    # rules:changes is always true for a branch's first push, so the first main pipeline
    # would run promote with no models/champion.json and fail without the exists clause.
    ci = _ci()
    for job in ("promote", "deploy_production"):
        (rule,) = ci[job]["rules"]
        assert rule["changes"] == ["models/champion.json"]
        assert rule["exists"] == ["models/champion.json"]
