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
