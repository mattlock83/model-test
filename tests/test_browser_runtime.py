"""Verify browser ownership, startup and cleanup without launching a real browser."""

import os
import subprocess
import sys
from dataclasses import replace
from unittest.mock import Mock

import pytest

from testwalker import browser_runtime as runtime
from testwalker.config import RuntimeConfig


@pytest.fixture
def config(tmp_path):
    return RuntimeConfig(
        path=tmp_path / "runtime.properties",
        graphwalker=tmp_path / "graphwalker",
        api_key="unused-unit-key",
        chrome_executable=tmp_path / "Chrome with spaces",
        chrome_profile=tmp_path / "isolated profile",
        chrome_port=9333,
    )


def test_existing_connection_is_reused_and_environment_restored(config, monkeypatch):
    monkeypatch.setattr(runtime, "ready", lambda _: True)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *_a, **_k: pytest.fail("Must reuse Chrome"))
    monkeypatch.setenv("BU_NAME", "previous-session")
    monkeypatch.setenv("BU_CDP_URL", "http://previous.test:9222")
    with runtime.connect(config, cdp_url="http://external.test:9444") as url:
        assert url == "http://external.test:9444"
        assert os.environ["BU_CDP_URL"] == url
        assert os.environ["BU_NAME"].startswith("testwalker-")
    assert os.environ["BU_NAME"] == "previous-session"
    assert os.environ["BU_CDP_URL"] == "http://previous.test:9222"


@pytest.mark.parametrize("headed,keep_open", [(False, False), (True, False), (True, True)])
def test_cli_launch_uses_isolated_profile_and_respects_browser_ownership(
    config, monkeypatch, headed, keep_open
):
    responses = iter([False, True])
    monkeypatch.setattr(runtime, "ready", lambda _: next(responses))
    monkeypatch.setattr(runtime, "port_occupied", lambda _: False)
    process = Mock()
    launch = Mock(return_value=process)
    cleanup = Mock()
    monkeypatch.setattr(runtime.subprocess, "Popen", launch)
    monkeypatch.setattr(runtime, "stop", cleanup)
    monkeypatch.delenv("BU_NAME", raising=False)
    monkeypatch.delenv("BU_CDP_URL", raising=False)
    with runtime.connect(config, headed=headed, keep_browser_open=keep_open) as url:
        assert url == "http://127.0.0.1:9333"
        assert config.chrome_profile.is_dir()
        arguments = launch.call_args.args[0]
        assert arguments[0] == str(config.chrome_executable)
        assert f"--user-data-dir={config.chrome_profile}" in arguments
        assert "--remote-debugging-address=127.0.0.1" in arguments
        assert ("--headless=new" in arguments) is not headed
        assert launch.call_args.kwargs.get("shell", False) is False
        assert not cleanup.called
    assert cleanup.called is not keep_open
    assert "BU_NAME" not in os.environ and "BU_CDP_URL" not in os.environ


@pytest.mark.parametrize("headed", [False, True])
@pytest.mark.parametrize("keep_open", [False, True])
def test_failed_start_is_cleaned_up_even_for_visible_browser(config, monkeypatch, headed, keep_open):
    monkeypatch.setattr(runtime, "ready", lambda _: False)
    monkeypatch.setattr(runtime, "port_occupied", lambda _: False)
    process = Mock()
    process.poll.return_value = 1
    monkeypatch.setattr(runtime.subprocess, "Popen", Mock(return_value=process))
    cleanup = Mock()
    monkeypatch.setattr(runtime, "stop", cleanup)
    with pytest.raises(ValueError, match="did not expose"):
        with runtime.connect(config, headed=headed, keep_browser_open=keep_open):
            pytest.fail("Failed startup must not run tests")
    cleanup.assert_called_once_with(process)


def test_visible_browser_is_closed_after_run_exception(config, monkeypatch):
    responses = iter([False, True])
    monkeypatch.setattr(runtime, "ready", lambda _: next(responses))
    monkeypatch.setattr(runtime, "port_occupied", lambda _: False)
    process = Mock()
    monkeypatch.setattr(runtime.subprocess, "Popen", Mock(return_value=process))
    cleanup = Mock()
    monkeypatch.setattr(runtime, "stop", cleanup)
    with pytest.raises(RuntimeError, match="run failed"):
        with runtime.connect(config, headed=True):
            raise RuntimeError("run failed")
    cleanup.assert_called_once_with(process)


def test_explicit_unavailable_endpoint_does_not_launch_chrome(config, monkeypatch):
    monkeypatch.setattr(runtime, "ready", lambda _: False)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *_a, **_k: pytest.fail("Explicit means external"))
    with pytest.raises(ValueError, match="unavailable"):
        with runtime.connect(replace(config, cdp_url="http://external.test:9222")):
            pytest.fail("Must fail before running")


def test_unrelated_occupied_port_does_not_launch_chrome(config, monkeypatch):
    monkeypatch.setattr(runtime, "ready", lambda _: False)
    monkeypatch.setattr(runtime, "port_occupied", lambda _: True)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *_a, **_k: pytest.fail("Port is unavailable"))
    with pytest.raises(ValueError, match="occupied"):
        with runtime.connect(config):
            pytest.fail("Must fail before running")


def test_chrome_lookup_reports_actionable_config_error(config, monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="CHROME_EXECUTABLE or CDP_URL"):
        runtime.chrome_executable(replace(config, chrome_executable=None))


def test_chrome_lookup_uses_installed_executable(config, monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.shutil, "which", lambda _: sys.executable)
    assert runtime.chrome_executable(replace(config, chrome_executable=None)).is_file()


def test_stop_kills_process_only_after_graceful_shutdown_times_out():
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("chrome", 5), 0]
    runtime.stop(process)
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.wait.call_count == 2


def test_ready_requires_a_debugging_endpoint_and_bypasses_proxy(monkeypatch):
    from io import BytesIO

    opener = Mock()
    opener.open.return_value = BytesIO(b'{"webSocketDebuggerUrl":"ws://127.0.0.1/devtools/browser/abc"}')
    factory = Mock(return_value=opener)
    monkeypatch.setattr(runtime, "build_opener", factory)
    assert runtime.ready("http://127.0.0.1:9222/")
    opener.open.assert_called_once_with("http://127.0.0.1:9222/json/version", timeout=1)
    assert factory.call_args.args[0].proxies == {}
    opener.open.return_value = BytesIO(b'{"unrelated":"HTTP server"}')
    assert not runtime.ready("http://127.0.0.1:9222")
    opener.open.side_effect = OSError("unavailable")
    assert not runtime.ready("http://127.0.0.1:9222")


def test_explicit_connection_overrides_inherited_websocket_and_cloud_settings(config, monkeypatch):
    monkeypatch.setattr(runtime, "ready", lambda _: True)
    monkeypatch.setenv("BU_CDP_WS", "ws://wrong-browser.test/devtools")
    monkeypatch.setenv("BU_BROWSER_ID", "unrelated-cloud-browser")
    with runtime.connect(config):
        websocket = os.environ.get("BU_CDP_WS")
        cloud_id = os.environ.get("BU_BROWSER_ID")
        assert not websocket and not cloud_id
    assert os.environ["BU_CDP_WS"] == "ws://wrong-browser.test/devtools"
    assert os.environ["BU_BROWSER_ID"] == "unrelated-cloud-browser"


def test_invalid_cli_endpoint_is_rejected_before_readiness_check(config, monkeypatch):
    monkeypatch.setattr(runtime, "ready", lambda _: pytest.fail("Invalid URL must fail before connection"))
    with pytest.raises(ValueError, match="CDP_URL"):
        with runtime.connect(config, cdp_url="http://localhost:not-a-port"):
            pytest.fail("Invalid endpoint must not be used")
