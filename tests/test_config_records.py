"""test_config_records.py — .conf 记录到类型化 Account/Project 的映射（单一形状）。"""
from __future__ import annotations

from pathlib import Path

from coderfleet.account_type_registry import duplicate_account_types
from coderfleet.config import parse_conf, parse_optional_int, truthy
from coderfleet.server.models import Account, AccountAuth, AccountType, Project
from coderfleet.server.scheduler import Scheduler


# ── 强制转换器 ─────────────────────────────────────────────────────────
def test_truthy_and_parse_optional_int() -> None:
    assert truthy("on") and truthy("1") and truthy("TRUE") and truthy("yes")
    assert not truthy("off") and not truthy("") and not truthy("nope")
    assert parse_optional_int("7") == 7
    assert parse_optional_int("") is None
    assert parse_optional_int("x") is None


# ── Account.from_conf_record ───────────────────────────────────────────
def test_account_record_valid() -> None:
    acc = Account.from_conf_record({"NAME": "alice", "TYPE": "claude"})
    assert acc is not None
    assert acc.name == "alice" and acc.type == AccountType.claude
    assert acc.auth == AccountAuth.login


def test_account_record_missing_fields_and_bad_enum() -> None:
    assert Account.from_conf_record({"NAME": "a"}) is None          # 缺 TYPE
    assert Account.from_conf_record({"TYPE": "claude"}) is None      # 缺 NAME
    assert Account.from_conf_record({"NAME": "a", "TYPE": "nope"}) is None  # 非法 TYPE


def test_account_record_env_auth_defaults_env_file() -> None:
    # env-auth 且未给 ENV_FILE → 自动补默认路径（对支持 env 的类型）
    from coderfleet.account_type_registry import env_auth_type_ids
    ids = env_auth_type_ids()
    if ids:
        acc = Account.from_conf_record({"NAME": "bob", "TYPE": ids[0], "AUTH": "env"})
        assert acc is not None
        assert acc.env_file == "./accounts/bob/env"


# ── Project.from_conf_record ───────────────────────────────────────────
def test_project_record_defaults() -> None:
    proj = Project.from_conf_record({"NAME": "web", "ACCOUNT": "alice", "PATH": "/srv/web"})
    assert proj is not None
    assert proj.active is True and proj.ide_enabled is False
    assert proj.ide_port is None and proj.ide_auth == "none"


def test_project_record_flags_and_home_expansion() -> None:
    proj = Project.from_conf_record({
        "NAME": "web", "ACCOUNT": "alice", "PATH": "~/code",
        "ACTIVE": "off", "IDE": "on", "IDE_PORT": "8443", "EPHEMERAL": "on",
    })
    assert proj is not None
    assert proj.active is False and proj.ide_enabled is True
    assert proj.ide_port == 8443 and proj.ephemeral is True
    assert proj.path.startswith(str(Path.home())) and "~" not in proj.path


def test_project_record_missing_required() -> None:
    assert Project.from_conf_record({"NAME": "web", "ACCOUNT": "alice"}) is None  # 缺 PATH


def test_project_record_parses_secondary_accounts() -> None:
    proj = Project.from_conf_record({
        "NAME": "web", "ACCOUNT": "alice", "PATH": "/srv/web",
        "SECONDARY_ACCOUNTS": "bob, carol",
    })
    assert proj is not None
    assert proj.secondary_accounts == ["bob", "carol"]


def test_project_record_defaults_secondary_accounts_to_empty() -> None:
    proj = Project.from_conf_record({"NAME": "web", "ACCOUNT": "alice", "PATH": "/srv/web"})
    assert proj is not None
    assert proj.secondary_accounts == []


# ── duplicate_account_types（primary/secondary 类型互斥校验） ──────────
def test_duplicate_account_types_none_when_all_distinct() -> None:
    assert duplicate_account_types(["claude", "codex"]) == []


def test_duplicate_account_types_flags_primary_secondary_collision() -> None:
    assert duplicate_account_types(["claude", "claude"]) == ["claude"]


def test_duplicate_account_types_flags_secondary_secondary_collision() -> None:
    assert duplicate_account_types(["claude", "codex", "codex"]) == ["codex"]


# ── 端到端：scheduler 读取 .conf → 类型化对象 ──────────────────────────
def test_scheduler_reads_conf_into_typed_models(tmp_path: Path) -> None:
    (tmp_path / "accounts.conf").write_text(
        "# 注释\nNAME=alice  TYPE=claude\nNAME=bob  TYPE=nope\n", encoding="utf-8"
    )
    (tmp_path / "projects.conf").write_text(
        "NAME=web  ACCOUNT=alice  PATH=/srv/web  IDE=on  IDE_PORT=8443\n", encoding="utf-8"
    )
    sched = Scheduler(tmp_path)

    accounts = sched.get_accounts()
    assert [a.name for a in accounts] == ["alice"]      # 非法 TYPE 的 bob 被跳过
    assert accounts[0].type == AccountType.claude

    projects = sched.get_projects()
    assert len(projects) == 1
    assert projects[0].name == "web" and projects[0].ide_port == 8443

    # parse_conf 仍是唯一的词法层
    assert parse_conf(tmp_path / "accounts.conf")[0]["NAME"] == "alice"
