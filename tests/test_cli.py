from model_test import cli


def test_live_demo_requires_key_and_has_no_offline_fallback(monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda *_: None)
    assert cli.main(["demo"]) == 2
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err
    assert "--offline" not in cli.parser().format_help()
