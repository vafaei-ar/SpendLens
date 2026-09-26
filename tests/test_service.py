import plistlib
from pathlib import Path

from spendlens.service import (
    SERVICE_LABEL,
    build_plist,
    launch_agent_path,
    service_log_dir,
)


def test_build_plist_uses_python_project_and_keepalive(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "SpendLens"
    project.mkdir()

    payload = build_plist(
        project_dir=project,
        python_executable="/opt/python/bin/python",
        home=home,
    )

    assert payload["Label"] == SERVICE_LABEL
    assert payload["ProgramArguments"] == [
        "/opt/python/bin/python",
        "-m",
        "spendlens.bot",
    ]
    assert payload["WorkingDirectory"] == str(project.resolve())
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert payload["ProcessType"] == "Background"
    assert payload["EnvironmentVariables"] == {"PYTHONUNBUFFERED": "1"}

    log_dir = service_log_dir(home)
    assert payload["StandardOutPath"] == str(log_dir / "stdout.log")
    assert payload["StandardErrorPath"] == str(log_dir / "stderr.log")


def test_launch_agent_path_is_user_scoped(tmp_path: Path) -> None:
    path = launch_agent_path(tmp_path)
    assert path == (
        tmp_path
        / "Library"
        / "LaunchAgents"
        / "com.spendlens.bot.plist"
    )


def test_generated_plist_is_serializable(tmp_path: Path) -> None:
    project = tmp_path / "SpendLens"
    project.mkdir()

    payload = build_plist(
        project_dir=project,
        python_executable="/usr/bin/python3",
        home=tmp_path / "home",
    )

    encoded = plistlib.dumps(payload, fmt=plistlib.FMT_XML)
    decoded = plistlib.loads(encoded)

    assert decoded["Label"] == SERVICE_LABEL
    assert decoded["KeepAlive"] is True



def test_service_log_redacts_telegram_bot_token() -> None:
    from spendlens.service import _redact_log_line

    line = (
        "POST https://api.telegram.org/"
        "bot123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdef123456/getUpdates"
    )
    redacted = _redact_log_line(line)

    assert "123456789:" not in redacted
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in redacted
    assert "bot<redacted>/getUpdates" in redacted


def test_restart_uses_kickstart_when_loaded(monkeypatch) -> None:
    import spendlens.service as service

    calls: list[list[str]] = []

    monkeypatch.setattr(service, "_require_macos", lambda: None)
    monkeypatch.setattr(
        service,
        "launch_agent_path",
        lambda: Path("/tmp/com.spendlens.bot.plist"),
    )
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(service, "_is_loaded", lambda: True)

    def fake_run(args, *, check=True):
        calls.append(list(args))
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""
        return Result()

    monkeypatch.setattr(service, "_run", fake_run)

    message = service.restart()

    assert message == "SpendLens service restarted."
    assert calls == [
        [
            "launchctl",
            "kickstart",
            "-k",
            "gui/501/com.spendlens.bot",
        ]
    ]
