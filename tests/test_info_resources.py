"""Regression coverage for the info package inventory."""

import grp
import os
import pwd
from importlib.metadata import version as distribution_version

import pytest

from science_assistant.commands.info_resources import (
    _group_name,
    _path_info,
    _user_name,
    package_info,
    runtime_info,
)


def test_adapter_wheel_path_tracks_installed_adapter() -> None:
    adapter_version = distribution_version("mcp-proxy-adapter")
    assert package_info()["adapter_wheel_relative_path"] == (
        f"releases/science-assistant-client/mcp_proxy_adapter-{adapter_version}-py3-none-any.whl"
    )


def test_user_name_resolves_a_real_uid() -> None:
    assert _user_name(os.getuid()) == pwd.getpwuid(os.getuid()).pw_name


def test_group_name_resolves_a_real_gid() -> None:
    assert _group_name(os.getgid()) == grp.getgrgid(os.getgid()).gr_name


def test_user_name_degrades_to_none_without_a_matching_passwd_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A container started with `docker run --user <uid>:<gid>` (see docker/docker-run.sh)
    has no /etc/passwd entry for that numeric uid; info must report None, not crash."""

    def _raise(_uid: int) -> pwd.struct_passwd:
        raise KeyError("getpwuid(): uid not found: 999999")

    monkeypatch.setattr(pwd, "getpwuid", _raise)
    assert _user_name(999999) is None


def test_group_name_degrades_to_none_without_a_matching_group_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_gid: int) -> grp.struct_group:
        raise KeyError("getgrgid(): gid not found: 999999")

    monkeypatch.setattr(grp, "getgrgid", _raise)
    assert _group_name(999999) is None


def test_user_name_negative_uid_is_none_without_a_lookup() -> None:
    assert _user_name(-1) is None


def test_runtime_info_does_not_crash_without_a_matching_passwd_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reproduces the getpwuid crash seen after switching the container to
    `docker run --user <uid>:<gid>` with no NSS entry for that uid inside the
    image: runtime_info() must degrade gracefully, not raise."""

    def _raise_pwd(_uid: int) -> pwd.struct_passwd:
        raise KeyError("getpwuid(): uid not found")

    def _raise_grp(_gid: int) -> grp.struct_group:
        raise KeyError("getgrgid(): gid not found")

    monkeypatch.setattr(pwd, "getpwuid", _raise_pwd)
    monkeypatch.setattr(grp, "getgrgid", _raise_grp)
    monkeypatch.delenv("SCIENCE_ASSISTANT_USER", raising=False)
    monkeypatch.delenv("SCIENCE_ASSISTANT_GROUP", raising=False)
    info = runtime_info()
    assert info["process"]["user"] is None
    assert info["process"]["group"] is None
    assert info["process"]["uid"] == os.getuid()


def test_runtime_info_falls_back_to_declared_identity_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """docker/docker-run.sh starts the container with `--user <uid>:<gid>` and
    passes SCIENCE_ASSISTANT_USER/GROUP as the same identity's name, but bakes
    no matching /etc/passwd entry into the image. When NSS lookup fails,
    process.user/group must match expected_identity.user/group (both sourced
    from those env vars) so the packaging-standard identity check holds."""

    def _raise_pwd(_uid: int) -> pwd.struct_passwd:
        raise KeyError("getpwuid(): uid not found")

    def _raise_grp(_gid: int) -> grp.struct_group:
        raise KeyError("getgrgid(): gid not found")

    monkeypatch.setattr(pwd, "getpwuid", _raise_pwd)
    monkeypatch.setattr(grp, "getgrgid", _raise_grp)
    monkeypatch.setenv("SCIENCE_ASSISTANT_USER", "scasuser")
    monkeypatch.setenv("SCIENCE_ASSISTANT_GROUP", "scasgrp")
    info = runtime_info()
    assert info["process"]["user"] == "scasuser" == info["expected_identity"]["user"]
    assert info["process"]["group"] == "scasgrp" == info["expected_identity"]["group"]


def test_path_info_does_not_crash_without_a_matching_passwd_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    def _raise_pwd(_uid: int) -> pwd.struct_passwd:
        raise KeyError("getpwuid(): uid not found")

    def _raise_grp(_gid: int) -> grp.struct_group:
        raise KeyError("getgrgid(): gid not found")

    monkeypatch.setattr(pwd, "getpwuid", _raise_pwd)
    monkeypatch.setattr(grp, "getgrgid", _raise_grp)
    info = _path_info(tmp_path)  # type: ignore[arg-type]
    assert info["exists"] is True
    assert info["user"] is None
    assert info["group"] is None
