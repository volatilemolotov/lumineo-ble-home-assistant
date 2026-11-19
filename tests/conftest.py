"""Load the protocol modules straight from their files.

mesh.py and provision.py don't import Home Assistant, so these tests run without it.
Loading by path also avoids custom_components/lumineo/select.py shadowing the stdlib.
"""
import importlib.util
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "lumineo"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, PKG / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("mesh")
_load("provision")
