"""benchmark suite for C++20 modules, precompiled headers, and header-only builds in Vulkan-Hpp."""

from .cli import main, parse_args
from .constants import (
    CONFIGURATION_PALETTE,
    DEFAULT_CLANG,
    DEFAULT_CMAKE,
    METRIC_ATTRIBUTE_MAP,
    ROOT_DIR,
    SCENARIO_DEFINITIONS,
)
from .data import export_data_json, import_data_json, import_log
from .formatters import SIUnitFormatter
from .models import (
    BuildConfig,
    BuildRunResult,
    MetricComparison,
    MetricStats,
    ScenarioComparison,
    ScenarioStats,
    TargetExecution,
    TargetTypeBreakdown,
    TimedCommandResult,
    to_delta,
)
from .parsers import (
    find_latest_cmake_trace,
    parse_clang_ftime_trace,
    parse_cmake_instrumentation_trace,
    parse_ninja_build_log,
    resolve_event_trace_path,
)
from .plots import generate_svg_visualizations, render_grouped_boxplot
from .report import generate_html_report, generate_markdown_report
from .runner import (
    BenchmarkSuite,
    get_compiler_identifier,
    get_scenario_touch_file,
    run_timed_subprocess,
)
from .stats import (
    calculate_all_stats,
    compare_scenarios,
    compute_scenario_stats,
    make_comparison_row,
)

__all__ = [
    "ROOT_DIR",
    "DEFAULT_CMAKE",
    "DEFAULT_CLANG",
    "SCENARIO_DEFINITIONS",
    "METRIC_ATTRIBUTE_MAP",
    "CONFIGURATION_PALETTE",
    "to_delta",
    "TimedCommandResult",
    "BuildConfig",
    "TargetExecution",
    "TargetTypeBreakdown",
    "BuildRunResult",
    "MetricStats",
    "ScenarioStats",
    "MetricComparison",
    "ScenarioComparison",
    "SIUnitFormatter",
    "compute_scenario_stats",
    "calculate_all_stats",
    "compare_scenarios",
    "make_comparison_row",
    "parse_ninja_build_log",
    "parse_clang_ftime_trace",
    "find_latest_cmake_trace",
    "resolve_event_trace_path",
    "parse_cmake_instrumentation_trace",
    "export_data_json",
    "import_data_json",
    "import_log",
    "render_grouped_boxplot",
    "generate_svg_visualizations",
    "generate_html_report",
    "generate_markdown_report",
    "run_timed_subprocess",
    "get_compiler_identifier",
    "get_scenario_touch_file",
    "BenchmarkSuite",
    "parse_args",
    "main",
]
