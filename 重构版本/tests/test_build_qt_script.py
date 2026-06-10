from pathlib import Path


def test_build_qt_script_makes_web_build_opt_in_and_checks_native_exit_codes():
    script = Path("build_qt.ps1").read_text(encoding="utf-8")

    assert 'ROBOT_BUILD_WEB' in script
    assert '$env:ROBOT_BUILD_WEB -eq "1"' in script
    assert "npm.cmd found but ROBOT_BUILD_WEB is not 1; skip Web frontend build." in script
    assert script.count("$LASTEXITCODE") >= 3
    assert "PyInstaller failed" in script
