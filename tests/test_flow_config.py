"""Static checks on the Duo flow definition, the agent config and AGENTS.md.

These cannot prove the flow runs on GitLab; they catch the mistakes that would make the AI
Catalog reject it or make the agent call something that does not exist.
"""

from __future__ import annotations

import re

import pytest
import yaml
from conftest import REPO

from modelgate import flow

FLOW_FILE = REPO / ".gitlab" / "duo" / "flows" / "modelgate.yaml"
AGENT_CONFIG = REPO / ".gitlab" / "duo" / "agent-config.yml"

# Tool names from the Duo Workflow Service tool registry (duo_workflow_service/tools).
KNOWN_TOOLS = {
    "gitlab_api_get",
    "run_command",
    "read_file",
    "edit_file",
    "create_merge_request",
    "update_merge_request",
    "create_merge_request_note",
    "add_merge_request_reviewers",
    "get_merge_request",
    "list_dir",
    "find_files",
    "create_file_with_contents",
}


@pytest.fixture(scope="module")
def spec() -> dict:
    return yaml.safe_load(FLOW_FILE.read_text(encoding="utf-8"))


def test_size_limits() -> None:
    assert FLOW_FILE.stat().st_size <= 40 * 1024  # AI Catalog flow limit


def test_schema_basics(spec: dict) -> None:
    assert spec["version"] == "v1"
    assert spec["environment"] == "ambient"  # the only environment custom flows support
    names = {c["name"] for c in spec["components"]}
    assert spec["flow"]["entry_point"] in names
    for router in spec["routers"]:
        assert router["from"] in names
        assert router["to"] in names | {"end", "abort"}
    for component in spec["components"]:
        assert re.fullmatch(r"[A-Za-z0-9_]+", component["name"])


def test_prompts_and_inputs(spec: dict) -> None:
    prompts = {p["prompt_id"]: p for p in spec["prompts"]}
    for component in spec["components"]:
        prompt = prompts[component["prompt_id"]]
        template = prompt["prompt_template"]
        assert len((template["system"] + template["user"]).encode()) <= 80 * 1024
        for item in component["inputs"]:
            assert item["from"].startswith("context:")
            assert "{{" + item["as"] + "}}" in template["user"]


# GitLab rejects a flow whose prompts contain HTML comments, Markdown link comments, the
# <goal> or <system> tags, or hidden characters ("contains dangerous content").
FORBIDDEN_IN_PROMPTS = re.compile(
    r"<!--|-->|\[//\]: #|\[comment\]: <>|</?\s*(goal|system)\b|[^\x20-\x7e\t\n]", re.IGNORECASE
)


@pytest.mark.parametrize("name", ["modelgate.yaml", "modelgate-ask.yaml"])
def test_prompts_pass_gitlab_content_checks(name: str) -> None:
    spec = yaml.safe_load((FLOW_FILE.parent / name).read_text(encoding="utf-8"))
    for prompt in spec["prompts"]:
        for role, text in prompt["prompt_template"].items():
            found = FORBIDDEN_IN_PROMPTS.search(text)
            assert found is None, f"{prompt['prompt_id']} ({role}): {found.group()!r}"


def test_tools_exist(spec: dict) -> None:
    for component in spec["components"]:
        assert set(component["toolset"]) <= KNOWN_TOOLS


def test_prompt_uses_only_real_helper_commands(spec: dict) -> None:
    system = spec["prompts"][0]["prompt_template"]["system"]
    used = set(re.findall(r"-m modelgate\.flow ([a-z-]+)", system))
    parser_commands = {"prepare", "check", "commit", "verify-mr", "promotion-note"}
    assert used and used <= parser_commands
    # Every status the prompt branches on is one the helper can return.
    statuses = set(re.findall(r'status "([a-z_]+)"', system))
    assert statuses <= {
        "superseded",
        "no_candidate",
        "error",
        "ready",
        "rejected",
        "committed",
        "nothing_to_commit",
        "mismatch",
        "note",
        "no_merge_request",
        "no_promotion",
        "already_posted",
    }


def test_prompt_states_the_honesty_rule(spec: dict) -> None:
    system = spec["prompts"][0]["prompt_template"]["system"]
    assert "Never say a rule passed when" in system
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "Never say a rule passed when the report says it failed" in agents


def test_agent_config_keys() -> None:
    config = yaml.safe_load(AGENT_CONFIG.read_text(encoding="utf-8"))
    assert set(config) <= {"image", "setup_script", "network_policy", "cache"}
    assert "requirements.txt" in " ".join(config["setup_script"])
    assert config["cache"]["paths"]
    assert "*" not in config["network_policy"]["allowed_domains"]


def test_helper_names_match_the_ci_jobs() -> None:
    ci = yaml.safe_load(
        (REPO / ".gitlab-ci.yml").read_text(encoding="utf-8").replace("!reference", "")
    )
    for job in (flow.TRAIN_JOB, flow.REGISTER_JOB):
        assert job in ci, f"flow.py expects a CI job named {job}"


ASK_FILE = REPO / ".gitlab" / "duo" / "flows" / "modelgate-ask.yaml"


def test_ask_flow_is_small_read_only_and_valid() -> None:
    assert ASK_FILE.stat().st_size <= 40 * 1024
    ask = yaml.safe_load(ASK_FILE.read_text(encoding="utf-8"))
    assert (ask["version"], ask["environment"]) == ("v1", "ambient")
    (component,) = ask["components"]
    assert ask["flow"]["entry_point"] == component["name"]
    assert set(component["toolset"]) == {"gitlab_api_get", "create_merge_request_note"}
    prompt = next(p for p in ask["prompts"] if p["prompt_id"] == component["prompt_id"])
    system = prompt["prompt_template"]["system"]
    for item in component["inputs"]:
        assert "{{" + item["as"] + "}}" in prompt["prompt_template"]["user"]
    assert "Never say a rule passed when" in system
    assert "margin" in system and "models/decisions" in system
