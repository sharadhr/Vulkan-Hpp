#!/usr/bin/env python3
"""benchmark C++20 named modules against header-only and precompiled header builds in Vulkan-Hpp."""

from pathlib import Path
import sys

# ensure scripts directory is in sys.path for direct script execution
_scripts_directory = Path(__file__).resolve().parent
if str(_scripts_directory) not in sys.path:
    sys.path.insert(0, str(_scripts_directory))

from benchmark.cli import main

if __name__ == "__main__":
    main()
