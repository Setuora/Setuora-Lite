"""Run actual controller functions with mocked Windows effects when PowerShell is available.

Set SETUORA_TEST_PWSH to powershell.exe (Windows 5.1) or pwsh (PowerShell 7).
These tests never elevate, execute Git/deploy.py, open a browser, or install anything.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = PROJECT_ROOT / "client/windows/setuora.ps1"
HARNESS = PROJECT_ROOT / "tests/windows_controller_harness.ps1"
PORT_HARNESS = PROJECT_ROOT / "tests/windows_port_harness.ps1"
PORT_CLEARER = PROJECT_ROOT / "scripts/windows/clear-owned-port.ps1"


@pytest.fixture(scope="module")
def powershell():
    executable = (
        os.getenv("SETUORA_TEST_PWSH") or shutil.which("powershell.exe") or shutil.which("pwsh")
    )
    if not executable:
        pytest.skip("Set SETUORA_TEST_PWSH to run isolated PowerShell controller tests")
    return executable


@pytest.mark.parametrize(
    "case", ["parse", "dispatch", "elevation", "routing", "source-update", "tailnet", "serve-route", "saved-ports", "menu"]
)
def test_windows_controller_runtime_with_isolated_effects(powershell, case):
    result = subprocess.run(  # noqa: S603
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(HARNESS),
            "-Controller",
            str(CONTROLLER),
            "-Case",
            case,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"PASS:{case}" in result.stdout


def test_windows_controller_help_and_invalid_command_exit_codes(powershell):
    help_result = subprocess.run(  # noqa: S603
        [powershell, "-NoLogo", "-NoProfile", "-File", str(CONTROLLER), "help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr
    assert "Double-click setuora.bat" in help_result.stdout
    assert (
        "Commands: setup, start, stop, status, open, logs, preflight, update, uninstall, help"
        in help_result.stdout
    )
    invalid_result = subprocess.run(  # noqa: S603
        [powershell, "-NoLogo", "-NoProfile", "-File", str(CONTROLLER), "not-a-valid-command"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert invalid_result.returncode != 0
    assert "not-a-valid-command" in invalid_result.stdout + invalid_result.stderr


def test_windows_port_clearer_rejects_foreign_process(powershell):
    result = subprocess.run(  # noqa: S603
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(PORT_HARNESS),
            "-PortClearer",
            str(PORT_CLEARER),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:port-ownership" in result.stdout
