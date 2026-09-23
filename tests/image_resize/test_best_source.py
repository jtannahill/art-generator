import importlib.util, pathlib, sys, types
from unittest import mock

def _load():
    sys.modules.setdefault("boto3", types.SimpleNamespace(client=lambda *a, **k: mock.MagicMock()))
    spec = importlib.util.spec_from_file_location(
        "resize_handler", pathlib.Path(__file__).parents[2] / "lambdas/image_resize/handler.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_prefers_4k_master_when_present():
    h = _load()
    with mock.patch.object(h, "_exists", return_value=True):
        assert h._best_source("r1", "arctic-70n-20w") == "weather/r1/arctic-70n-20w/preview-4k.png"

def test_falls_back_to_2048_without_waiting_by_default():
    h = _load()
    with mock.patch.object(h, "_exists", return_value=False):
        assert h._best_source("r1", "s") == "weather/r1/s/preview-2048.png"
