"""Visualization tools for the existing synthetic traffic analysis outputs."""

import os
from importlib.util import find_spec
from pathlib import Path


def configure_proj_data():
    """Prefer Rasterio's bundled PROJ database over incompatible system installs."""
    spec = find_spec("rasterio")
    if spec is None or spec.origin is None:
        raise ImportError("Rasterio is required to build the visualization outputs.")
    proj_data = Path(spec.origin).parent / "proj_data"
    if not (proj_data / "proj.db").is_file():
        raise FileNotFoundError(f"Rasterio PROJ database not found: {proj_data / 'proj.db'}")
    os.environ["PROJ_LIB"] = str(proj_data)
    os.environ["PROJ_DATA"] = str(proj_data)


configure_proj_data()
