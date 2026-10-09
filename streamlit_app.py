"""Entry point for Streamlit Cloud.

Streamlit Community Cloud picks up `streamlit_app.py` at the repo root by
default. This file simply re-exports the real app which lives under
`app/ui/streamlit_app.py` to keep the project layout clean.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make sure the app package is importable regardless of where streamlit
# chose to run from
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Load and execute the real app module. Doing this with runpy avoids the
# double-execution Streamlit does when the file imports a submodule that
# also calls `st.set_page_config`.
import runpy  # noqa: E402

runpy.run_module("app.ui.streamlit_app", run_name="__main__")
