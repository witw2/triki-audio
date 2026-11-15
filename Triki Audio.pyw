"""Double-click to start Triki Audio without a console window."""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("triki_audio.py")), run_name="__main__")
