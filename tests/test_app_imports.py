"""Smoke test: the deployed AppDaemon app must actually import cleanly
against the real appdaemon package and the sunknee package on sys.path,
the same way AppDaemon itself would load it."""
import importlib.util
from pathlib import Path


def test_sunknee_app_imports():
    path = Path(__file__).parent.parent / "apps" / "sunknee_app.py"
    spec = importlib.util.spec_from_file_location("sunknee_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "SunKnee")
