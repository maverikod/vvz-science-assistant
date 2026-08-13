"""Regression coverage for the info package inventory."""

from importlib.metadata import version as distribution_version

from science_assistant.commands.info_resources import package_info


def test_adapter_wheel_path_tracks_installed_adapter() -> None:
    adapter_version = distribution_version("mcp-proxy-adapter")
    assert package_info()["adapter_wheel_relative_path"] == (
        f"releases/science-assistant-client/mcp_proxy_adapter-{adapter_version}-py3-none-any.whl"
    )
