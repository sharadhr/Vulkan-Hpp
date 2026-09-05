"""constants and configuration defaults for the Vulkan-Hpp benchmark suite."""

from pathlib import Path
import shutil

# repository root directory resolved relative to package location
ROOT_DIR: Path = Path(__file__).resolve().parent.parent.parent

# executable discovery fallbacks
DEFAULT_CMAKE: str = shutil.which("cmake") or "cmake"
DEFAULT_CLANG: str = shutil.which("clang++") or "clang++"

# defines benchmark scenarios with display titles; touch-root-interface serves as primary comparison
SCENARIO_DEFINITIONS: list[tuple[str, str]] = [
    ("clean", "Clean build (all targets)"),
    ("touch-root-interface", "Core interface rebuild (vulkan.cppm/vulkan.hpp)"),
    ("touch-core-header", "Core macros rebuild (vulkan_hpp_macros.hpp)"),
    ("touch-intermediate-interface", "Intermediate interface rebuild (utils.cppm/utils.hpp)"),
    ("touch-cpp", "Leaf translation unit rebuild (RayTracing.cpp)"),
]

# map metric keys to BuildRunResult field names for uniform batch evaluation
METRIC_ATTRIBUTE_MAP: dict[str, str] = {
    "compiler": "compiler_time",
    "frontend": "frontend_time",
    "backend": "backend_time",
    "wall": "wall_time",
}

# color palette for chart rendering across build configurations
CONFIGURATION_PALETTE: dict[str, dict[str, str]] = {
    "modules": {"face": "#3b82f640", "edge": "#1d4ed8"},
    "pch": {"face": "#10b98140", "edge": "#047857"},
    "headers": {"face": "#ef444440", "edge": "#b91c1c"},
}
