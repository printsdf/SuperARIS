"""SuperARIS entry and readable new-team layout, using fake Herdr only."""

import json
from pathlib import Path
import subprocess

from tests.test_aris_workbench import fake_herdr, project  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]
from tests.support import CLI
SUPERARIS = CLI


def invoke(entry, *args, env=None):
    return subprocess.run([*entry, *map(str, args)], env=env, capture_output=True,
                          text=True, timeout=20, check=False)


def team_start(project, fake, *options):
    return invoke(
        SUPERARIS, "start", project, "--session", "isolated-test", "--no-attach",
        "--no-install", "--no-receiver", "--herdr-bin", fake["binary"], *options,
        env=fake["env"],
    )


def test_installed_entry_has_help_and_offline_plan(project):
    arguments = ["start", project, "--plan", "--preset", "cc-codex-agy", "--only-role", "writer",
                 "--model", "writer=gemini-4-argon-high", "--task", "Existing approved task."]
    new = invoke(SUPERARIS, *arguments)
    assert new.returncode == 0, new.stderr
    assert json.loads(new.stdout)["mode"] == "plan"
    assert not (project / ".aris/workbench").exists()
    for entry, name in ((SUPERARIS, "superaris"),):
        result = invoke(entry, "--help")
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith(f"usage: {name} ")


def test_new_three_role_team_gets_readable_names_and_right_column_layout(project, fake_herdr):
    result = team_start(
        project, fake_herdr, "--preset", "cc-codex-agy", "--only-role", "main",
        "--only-role", "engineer", "--only-role", "writer",
    )
    assert result.returncode == 0, result.stderr
    roles = json.loads(result.stdout)["roles"]
    commands = json.loads(fake_herdr["state"].read_text())["commands"]
    workspace, = [args for args in commands if args[:2] == ["workspace", "create"]]
    assert workspace[workspace.index("--label") + 1] == "SuperARIS " + project.name
    assert ["tab", "rename", "w1:t1", "SuperARIS · Workbench"] in commands
    expected_labels = {"main": "Claude Code", "engineer": "Codex", "writer": "Antigravity"}
    for role, label in expected_labels.items():
        assert ["pane", "rename", roles[role]["pane_id"], f"{role} · {label}"] in commands
        assert roles[role]["name"].startswith("superaris-")
        assert len(roles[role]["name"]) <= 32
    splits = [args for args in commands if args[:2] == ["pane", "split"]]
    assert [(args[2], args[args.index("--direction") + 1]) for args in splits] == [
        (roles["main"]["pane_id"], "right"), (roles["engineer"]["pane_id"], "down"),
    ]
    assert len({role["name"] for role in roles.values()}) == 3


def test_reentering_existing_team_preserves_names_and_topology(project, fake_herdr):
    options = ["--preset", "cc-codex-agy", "--only-role", "main", "--only-role", "engineer", "--only-role", "writer"]
    initial = team_start(project, fake_herdr, *options)
    assert initial.returncode == 0, initial.stderr
    state = json.loads(fake_herdr["state"].read_text())
    count = len(state["commands"])
    result = team_start(project, fake_herdr, *options)
    assert result.returncode == 0, result.stderr
    original_roles, reused_roles = json.loads(initial.stdout)["roles"], json.loads(result.stdout)["roles"]
    for name in original_roles:
        assert reused_roles[name]["pane_id"] == original_roles[name]["pane_id"]
        assert reused_roles[name]["name"] == original_roles[name]["name"]
    added = json.loads(fake_herdr["state"].read_text())["commands"][count:]
    assert not any(args[:2] in (["workspace", "create"], ["tab", "create"], ["pane", "split"],
                               ["tab", "rename"], ["pane", "rename"], ["agent", "start"]) for args in added)


def test_adopted_owner_retains_legacy_name_and_original_topology(project, fake_herdr):
    state = json.loads(fake_herdr["state"].read_text())
    state["agents"]["legacy-owner"] = {
        "name": "legacy-owner", "pane_id": "w8:p7", "tab_id": "w8:t3", "workspace_id": "w8",
        "agent": "codex", "agent_status": "idle", "pid": 4321,
        "argv": ["codex", "--cd", str(project)], "cwd": str(project.parent),
    }
    fake_herdr["state"].write_text(json.dumps(state))
    result = team_start(project, fake_herdr, "--only-role", "main", "--owner", "legacy-owner")
    assert result.returncode == 0, result.stderr
    owner = json.loads(result.stdout)["roles"]["main"]
    assert owner["name"] == "legacy-owner"
    assert owner["pane_id"] == "w8:p7"
    assert owner["created"] is False
    commands = json.loads(fake_herdr["state"].read_text())["commands"]
    assert not any(args[:2] in (["workspace", "create"], ["tab", "create"], ["pane", "split"],
                               ["tab", "rename"], ["pane", "rename"], ["agent", "start"]) for args in commands)


def test_omp_harness_is_selectable_and_started_as_omp(project, fake_herdr):
    plan = invoke(SUPERARIS, "start", project, "--plan", "--role", "writer=omp")
    assert plan.returncode == 0, plan.stderr
    assert {role["role"]: role["harness"] for role in json.loads(plan.stdout)["roles"]}["writer"] == "omp"

    result = team_start(project, fake_herdr, "--role", "main=omp", "--only-role", "main")
    assert result.returncode == 0, result.stderr
    roles = json.loads(result.stdout)["roles"]
    assert roles["main"]["harness"] == "omp"
    commands = json.loads(fake_herdr["state"].read_text())["commands"]
    started = next(args for args in commands if args[:2] == ["agent", "start"])
    assert started[started.index("--kind") + 1] == "omp"
    assert started[-1] == roles["main"]["context"]["prompt"]


def _mark_agent_gone(fake, roles, role):
    state = json.loads(fake["state"].read_text())
    record = roles[role]
    agent = next(item for item in state["agents"].values() if item["pane_id"] == record["pane_id"])
    state["agents"].pop(agent["name"])
    state.setdefault("missing_agents", []).append(record["pane_id"])
    state.setdefault("shell_panes", []).append(record["pane_id"])
    fake["state"].write_text(json.dumps(state))


def test_reentry_restarts_a_harness_that_exited(project, fake_herdr):
    options = ["--preset", "cc-codex", "--only-role", "main", "--only-role", "engineer", "--only-role", "writer"]
    first = team_start(project, fake_herdr, *options)
    assert first.returncode == 0, first.stderr
    before = json.loads(first.stdout)["roles"]
    _mark_agent_gone(fake_herdr, before, "engineer")
    starts_before = sum(command[:2] == ["agent", "start"]
                        for command in json.loads(fake_herdr["state"].read_text())["commands"])

    retry = team_start(project, fake_herdr)
    assert retry.returncode == 0, retry.stderr
    result = json.loads(retry.stdout)
    assert result["roles"]["engineer"]["pane_id"] == before["engineer"]["pane_id"]
    commands = json.loads(fake_herdr["state"].read_text())["commands"]
    starts = [command for command in commands if command[:2] == ["agent", "start"]]
    assert len(starts) == starts_before + 1
    assert starts[-1][starts[-1].index("--kind") + 1] == "codex"
    assert starts[-1][starts[-1].index("--pane") + 1] == before["engineer"]["pane_id"]
    assert {"restarting_missing", "started"} <= {item.get("action") for item in result["actions"]}


def test_live_role_refuses_harness_change_until_old_client_exits(project, fake_herdr):
    options = ["--preset", "cc-codex", "--only-role", "main", "--only-role", "engineer", "--only-role", "writer"]
    first = team_start(project, fake_herdr, *options)
    assert first.returncode == 0, first.stderr
    before = json.loads(first.stdout)["roles"]
    commands_before = len(json.loads(fake_herdr["state"].read_text())["commands"])

    refused = team_start(project, fake_herdr, "--role", "writer=omp", "--only-role", "writer")
    assert refused.returncode != 0
    assert "still runs claude" in refused.stderr
    assert "exit that agent" in refused.stderr
    after_refusal = json.loads(fake_herdr["state"].read_text())
    assert not any(command[:2] in (["agent", "start"], ["agent", "prompt"])
                   for command in after_refusal["commands"][commands_before:])
    assert json.loads((project / ".aris/workbench/launch.json").read_text())["roles"]["writer"]["harness"] == "claude"

    _mark_agent_gone(fake_herdr, before, "writer")
    switched = team_start(project, fake_herdr, "--role", "writer=omp", "--only-role", "writer")
    assert switched.returncode == 0, switched.stderr
    result = json.loads(switched.stdout)
    assert result["roles"]["writer"]["harness"] == "omp"
    assert result["roles"]["writer"]["pane_id"] == before["writer"]["pane_id"]
    assert "harness_changed" in {item.get("action") for item in result["actions"]}
    starts = [command for command in json.loads(fake_herdr["state"].read_text())["commands"]
              if command[:2] == ["agent", "start"]]
    assert starts[-1][starts[-1].index("--kind") + 1] == "omp"
    assert starts[-1][starts[-1].index("--pane") + 1] == before["writer"]["pane_id"]


def test_pending_start_retries_a_stale_role_and_clears_pending(project, fake_herdr):
    options = ["--preset", "cc-codex", "--only-role", "main", "--only-role", "engineer", "--only-role", "writer"]
    first = team_start(project, fake_herdr, *options)
    assert first.returncode == 0, first.stderr
    roles = json.loads(first.stdout)["roles"]
    binding_path = project / ".aris/workbench/launch.json"
    binding = json.loads(binding_path.read_text())
    binding["pending_start"] = {
        "arguments": ["start", str(project), "--session", "isolated-test",
                      "--role", "main=claude", "--role", "engineer=codex", "--role", "writer=claude"],
        "roles": [{"role": role, "harness": record["harness"], "task": record["inputs"]["task"],
                   "write_scope": record["inputs"]["write_scope"], "active": True}
                  for role, record in binding["roles"].items()],
    }
    binding_path.write_text(json.dumps(binding))
    _mark_agent_gone(fake_herdr, roles, "engineer")

    retry = team_start(project, fake_herdr)
    assert retry.returncode == 0, retry.stderr
    final = json.loads(binding_path.read_text())
    assert "pending_start" not in final
    assert final["roles"]["engineer"]["pane_id"] == roles["engineer"]["pane_id"]
    assert "restarting_missing" in {item.get("action") for item in json.loads(retry.stdout)["actions"]}
