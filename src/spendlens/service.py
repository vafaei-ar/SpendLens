import argparse
import os
import plistlib
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


SERVICE_LABEL = "com.spendlens.bot"
PLIST_FILENAME = f"{SERVICE_LABEL}.plist"


class ServiceError(RuntimeError):
    pass


def _require_macos() -> None:
    if sys.platform != "darwin":
        raise ServiceError(
            "SpendLens service management currently supports macOS launchd only."
        )


def _domain(uid: int | None = None) -> str:
    return f"gui/{os.getuid() if uid is None else uid}"


def _target(uid: int | None = None) -> str:
    return f"{_domain(uid)}/{SERVICE_LABEL}"


def launch_agent_path(home: Path | None = None) -> Path:
    root = home or Path.home()
    return root / "Library" / "LaunchAgents" / PLIST_FILENAME


def service_log_dir(home: Path | None = None) -> Path:
    root = home or Path.home()
    return root / "Library" / "Logs" / "SpendLens"


def build_plist(
    *,
    project_dir: Path,
    python_executable: str,
    home: Path | None = None,
) -> dict[str, object]:
    project_dir = project_dir.expanduser().resolve()
    logs = service_log_dir(home)
    return {
        "Label": SERVICE_LABEL,
        "ProgramArguments": [
            python_executable,
            "-m",
            "spendlens.bot",
        ],
        "WorkingDirectory": str(project_dir),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "stdout.log"),
        "StandardErrorPath": str(logs / "stderr.log"),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
        },
    }


def _run(
    args: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=check,
        capture_output=True,
        text=True,
    )


def _is_loaded() -> bool:
    result = _run(
        ["launchctl", "print", _target()],
        check=False,
    )
    return result.returncode == 0


def _bootstrap(plist_path: Path) -> None:
    _run(
        [
            "launchctl",
            "bootstrap",
            _domain(),
            str(plist_path),
        ]
    )


def _bootout() -> None:
    _run(
        ["launchctl", "bootout", _target()],
        check=False,
    )


def install(
    *,
    project_dir: Path,
    python_executable: str | None = None,
) -> str:
    _require_macos()

    project_dir = project_dir.expanduser().resolve()
    env_path = project_dir / ".env"
    if not env_path.exists():
        raise ServiceError(
            f"SpendLens .env was not found at {env_path}. "
            "Create it before installing the service."
        )

    executable = python_executable or sys.executable
    plist_path = launch_agent_path()
    logs = service_log_dir()

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    payload = build_plist(
        project_dir=project_dir,
        python_executable=executable,
    )

    if _is_loaded():
        _bootout()

    plist_path.write_bytes(
        plistlib.dumps(payload, fmt=plistlib.FMT_XML)
    )
    os.chmod(plist_path, 0o600)
    _bootstrap(plist_path)

    return (
        "SpendLens service installed and started.\n"
        f"LaunchAgent: {plist_path}\n"
        f"Python: {executable}\n"
        f"Project: {project_dir}\n"
        "It will start automatically when you log in."
    )


def start() -> str:
    _require_macos()
    plist_path = launch_agent_path()
    if not plist_path.exists():
        raise ServiceError(
            "SpendLens service is not installed. "
            "Run: spendlens-service install"
        )

    if _is_loaded():
        _run(["launchctl", "kickstart", _target()])
        return "SpendLens service is already loaded; start requested."

    _bootstrap(plist_path)
    return "SpendLens service started."


def stop() -> str:
    _require_macos()
    if not _is_loaded():
        return "SpendLens service is already stopped."

    _bootout()
    return "SpendLens service stopped."


def restart() -> str:
    _require_macos()
    plist_path = launch_agent_path()
    if not plist_path.exists():
        raise ServiceError(
            "SpendLens service is not installed. "
            "Run: spendlens-service install"
        )

    if _is_loaded():
        _bootout()
    _bootstrap(plist_path)
    return "SpendLens service restarted."


def uninstall() -> str:
    _require_macos()
    plist_path = launch_agent_path()

    if _is_loaded():
        _bootout()

    if plist_path.exists():
        plist_path.unlink()
        return "SpendLens service stopped and uninstalled."

    return "SpendLens service was not installed."


def status() -> str:
    _require_macos()
    plist_path = launch_agent_path()
    if not plist_path.exists():
        return "SpendLens service: not installed"

    result = _run(
        ["launchctl", "print", _target()],
        check=False,
    )
    if result.returncode != 0:
        return (
            "SpendLens service: installed but stopped\n"
            f"LaunchAgent: {plist_path}"
        )

    state_match = re.search(
        r"^\s*state = ([^\n]+)",
        result.stdout,
        re.MULTILINE,
    )
    pid_match = re.search(
        r"^\s*pid = (\d+)",
        result.stdout,
        re.MULTILINE,
    )

    state = state_match.group(1).strip() if state_match else "loaded"
    pid = f" (pid {pid_match.group(1)})" if pid_match else ""
    return (
        f"SpendLens service: {state}{pid}\n"
        f"LaunchAgent: {plist_path}\n"
        f"Logs: {service_log_dir()}"
    )


def _tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return ["(no log file yet)"]
    content = path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()
    return content[-lines:]


def logs(lines: int = 80) -> str:
    _require_macos()
    if lines < 1:
        raise ServiceError("Log line count must be at least 1.")

    log_dir = service_log_dir()
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"

    output = [
        f"== {stdout_path} ==",
        *_tail(stdout_path, lines),
        "",
        f"== {stderr_path} ==",
        *_tail(stderr_path, lines),
    ]
    return "\n".join(output)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spendlens-service",
        description="Manage SpendLens as a macOS launchd service.",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    install_parser = subparsers.add_parser(
        "install",
        help="Install and start the LaunchAgent.",
    )
    install_parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path.cwd(),
        help="SpendLens project directory containing .env.",
    )
    install_parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used by launchd.",
    )

    subparsers.add_parser("start", help="Start the service.")
    subparsers.add_parser("stop", help="Stop the service.")
    subparsers.add_parser("restart", help="Restart the service.")
    subparsers.add_parser("status", help="Show service status.")
    subparsers.add_parser("uninstall", help="Stop and remove the service.")

    logs_parser = subparsers.add_parser(
        "logs",
        help="Show recent stdout and stderr logs.",
    )
    logs_parser.add_argument(
        "-n",
        "--lines",
        type=int,
        default=80,
        help="Number of lines to show from each log.",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        if args.command == "install":
            message = install(
                project_dir=args.project_dir,
                python_executable=args.python,
            )
        elif args.command == "start":
            message = start()
        elif args.command == "stop":
            message = stop()
        elif args.command == "restart":
            message = restart()
        elif args.command == "status":
            message = status()
        elif args.command == "logs":
            message = logs(args.lines)
        elif args.command == "uninstall":
            message = uninstall()
        else:
            parser.error(f"Unknown command: {args.command}")
            return
    except (ServiceError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Error: {exc}\n")

    print(message)


if __name__ == "__main__":
    main()
