# Shim so older pip versions (such as the one bundled with macOS's Python 3.9) can run
# `pip install -e .`. All project metadata lives in pyproject.toml.
from setuptools import setup

setup()
