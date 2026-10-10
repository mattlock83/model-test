import sys
from contextlib import contextmanager

import pytest

from testwalker import cli
from testwalker.resources import asset


def test_cli_defaults_to_focused_boundaries():
    args = cli.parser().parse_args(["demo"])
    assert args.input_mode == "focused" and args.cases == 1


def test_invalid_strategy_field_does_not_start_chrome(tmp_path, monkeypatch, capsys):
    policy = tmp_path / "inputs.json"
    policy.write_text('{"data sets": {"Unknown dataset": {}}}')
    monkeypatch.setattr(
        cli, "connect", lambda *_a, **_k: pytest.fail("Invalid strategies must not start Chrome")
    )
    result = cli.main(["demo", "--config", config_file(tmp_path), "--input-strategies", str(policy)])
    assert result == 2
    assert "Unknown strategy data set" in capsys.readouterr().err


def config_file(tmp_path, key="unit-test-key"):
    path = tmp_path / "runtime.properties"
    path.write_text(f"GRAPHWALKER_BIN={sys.executable}\nTYPESAFE_API_KEY={key}\n")
    return str(path)


def test_live_demo_requires_property_key_and_has_no_offline_fallback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ambient-key-must-not-win")
    assert cli.main(["demo", "--config", config_file(tmp_path, key="")]) == 2
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err
    commands = cli.parser()._subparsers._group_actions[0].choices
    assert "setup" not in commands
    assert "--offline" not in cli.parser().format_help()


def test_native_plan_uses_property_executable_without_browser_or_key(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "connect", lambda *_args, **_kwargs: pytest.fail("Plan must not launch Chrome"))
    assert cli.main(["plan", "--config", config_file(tmp_path, key="")]) == 0


@pytest.mark.parametrize("status, exit_code", [("PASS", 0), ("FAIL", 1), ("INCONCLUSIVE", 2)])
def test_installed_demo_loads_only_explicit_bundled_hooks_and_preserves_status(
    tmp_path,
    monkeypatch,
    status,
    exit_code,
):
    from testwalker import engine

    captured = {}

    @contextmanager
    def connection(config, **kwargs):
        captured["connection"] = kwargs
        yield "http://local.test:9222"

    @contextmanager
    def server():
        yield "http://application.test"

    def run(model, url, **kwargs):
        captured.update(model=model, url=url, **kwargs)
        assert kwargs["core"].request("core.info")["protocol_version"] == "1"
        assert kwargs["browser"].text_config == {}
        return {"status": status}

    monkeypatch.setattr(cli, "connect", connection)
    monkeypatch.setattr(cli, "serve", server)
    monkeypatch.setattr(engine, "run", run)
    monkeypatch.chdir(tmp_path)
    assert (
        cli.main(
            [
                "demo",
                "--site",
                "trailhead",
                "--demo-hooks",
                "--headed",
                "--keep-browser-open",
                "--debug",
                "--config",
                config_file(tmp_path),
                "--input-mode",
                "none",
                "--max-steps",
                "1000",
            ]
        )
        == exit_code
    )
    assert captured["hooks"].path == str(asset("examples/trailhead/hooks.py").resolve())
    assert captured["connection"]["headed"]
    assert captured["connection"]["keep_browser_open"]
    assert captured["keep_browser_open"] and captured["debug"]


def test_config_failure_happens_before_loading_hooks_or_starting_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.Hooks, "load", lambda *_: pytest.fail("Unvalidated config must not load hooks"))
    monkeypatch.setattr(cli, "connect", lambda *_args, **_kwargs: pytest.fail("Must not open Chrome"))
    assert cli.main(["demo", "--config", str(tmp_path / "missing")]) == 2


def test_retaining_browser_requires_visible_mode_before_launch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "connect", lambda *_args, **_kwargs: pytest.fail("Must not open Chrome"))
    assert cli.main(["demo", "--keep-browser-open", "--config", config_file(tmp_path)]) == 2
    assert "--keep-browser-open requires --headed" in capsys.readouterr().err


def test_debug_reports_preflight_exception_when_no_run_report_exists(tmp_path, capsys):
    assert cli.main(["demo", "--debug", "--config", str(tmp_path / "missing")]) == 2
    output = capsys.readouterr().err
    assert "Traceback" in output and "Cannot read the properties file" in output


def test_fresh_cli_initializes_harness_after_configuring_connection(tmp_path):
    import subprocess
    import textwrap

    script = textwrap.dedent("""
        import os
        import sys
        from contextlib import contextmanager
        from types import ModuleType
        from testwalker import cli

        os.environ['BU_NAME'] = 'wrong-inherited-session'
        @contextmanager
        def connection(config, **kwargs):
            os.environ['BU_NAME'] = 'configured-session'
            os.environ['BU_CDP_URL'] = 'http://127.0.0.1:9222'
            yield os.environ['BU_CDP_URL']
        cli.connect = connection
        module = ModuleType('testwalker.engine')
        def run(model, url, **kwargs):
            from browser_harness import admin, helpers
            assert helpers.NAME == admin.NAME == 'configured-session'
            assert kwargs['core'].request('core.info')['protocol_version'] == '1'
            return {'status': 'PASS'}
        module.run = run
        sys.modules['testwalker.engine'] = module
        raise SystemExit(cli.main([
            'run', '--model', sys.argv[1], '--url', 'http://application.test',
            '--config', sys.argv[2],
        ]))
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(asset("models/booking.json")), config_file(tmp_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_invalid_application_url_does_not_start_chrome(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "connect", lambda *_a, **_k: pytest.fail("Invalid URL must not start Chrome"))
    result = cli.main(
        [
            "run",
            "--model",
            str(asset("models/booking.json")),
            "--url",
            "http://localhost:invalid",
            "--config",
            config_file(tmp_path),
        ]
    )
    assert result == 2
    assert "application URL" in capsys.readouterr().err
