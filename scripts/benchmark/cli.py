"""command-line interface and orchestration driver for the benchmark suite."""

import argparse
from pathlib import Path
import sys

from .constants import DEFAULT_CLANG, DEFAULT_CMAKE, ROOT_DIR, SCENARIO_DEFINITIONS
from .formatters import SIUnitFormatter
from .models import ScenarioComparison, ScenarioStats
from .plots import generate_svg_visualizations
from .report import generate_html_report
from .runner import BenchmarkSuite, get_compiler_identifier, get_scenario_touch_file
from .stats import compare_scenarios


def parse_args() -> argparse.Namespace:
    """configure and parse benchmark command-line arguments."""
    parser = argparse.ArgumentParser(description="Vulkan-Hpp C++20 modules vs header-only benchmark suite")
    parser.add_argument("--runs", type=int, default=10, help="Number of iterations per scenario (default: 10)")
    parser.add_argument("-j", "--jobs", type=int, default=None, help="Parallel build jobs for ninja (default: ninja default)")
    parser.add_argument("--cmake", type=str, default=DEFAULT_CMAKE, help="Path to cmake executable")
    parser.add_argument("--clang", type=str, default=DEFAULT_CLANG, help="Path to clang++ executable")
    parser.add_argument(
        "--build-base",
        type=Path,
        default=ROOT_DIR / "out" / "build",
        help="Base directory for build configs",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=ROOT_DIR / "out" / "benchmark",
        help="Directory for reports and traces",
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=["clean", "touch-root-interface", "touch-core-header", "touch-intermediate-interface", "touch-cpp"],
        help="Scenarios to run",
    )
    parser.add_argument(
        "--configs",
        nargs="+",
        default=["modules", "pch", "headers"],
        help="Configurations to run",
    )
    parser.add_argument(
        "--import-log",
        type=Path,
        default=None,
        help="Import run data from existing log file and skip builds",
    )
    parser.add_argument(
        "--import-data",
        type=Path,
        default=None,
        help="Import run data from JSON file and skip builds",
    )
    parser.add_argument(
        "--export-data",
        type=Path,
        default=None,
        help="Export run data to JSON file",
    )
    return parser.parse_args()


def print_primary_comparison(
    comparison: ScenarioComparison,
    formatter: SIUnitFormatter | None = None,
) -> None:
    """print primary benchmark comparison box to terminal."""
    unit_formatter = formatter or SIUnitFormatter(unit="s", precision=3)
    compiler_comparison = comparison.compiler
    wall_comparison = comparison.wall
    border = "=" * 78
    comparison_box = f"""
{border}
PRIMARY BENCHMARK COMPARISON: {comparison.scenario_title}
{border}
Compiler CPU time:
  Modules:             {unit_formatter.format(compiler_comparison.modules):>10}
  Precompiled headers: {unit_formatter.format(compiler_comparison.precompiled_headers):>10} ({compiler_comparison.speedup_versus_pch:.2f}× speedup with modules)
  Headers only:        {unit_formatter.format(compiler_comparison.headers_only):>10} ({compiler_comparison.speedup_versus_headers:.2f}× speedup with modules)
Wall-clock build time:
  Modules:             {unit_formatter.format(wall_comparison.modules):>10}
  Precompiled headers: {unit_formatter.format(wall_comparison.precompiled_headers):>10} ({wall_comparison.speedup_versus_pch:.2f}× speedup with modules)
  Headers only:        {unit_formatter.format(wall_comparison.headers_only):>10} ({wall_comparison.speedup_versus_headers:.2f}× speedup with modules)
{border}"""
    print(comparison_box)


def print_all_scenarios_summary(
    stats: dict[tuple[str, str], ScenarioStats],
    formatter: SIUnitFormatter | None = None,
) -> None:
    """print per-scenario compiler and wall clock summary lines."""
    unit_formatter = formatter or SIUnitFormatter(unit="s", precision=3)
    print("\nBenchmark results summary (all scenarios):")
    for (config_name, scenario_name), scenario_stats in sorted(stats.items()):
        print(
            f"  [{config_name:8s}] {scenario_name:32s} : {unit_formatter.format(scenario_stats.compiler.mean):>10} "
            f"(Frontend: {unit_formatter.format(scenario_stats.frontend.mean):>10}, Backend: {unit_formatter.format(scenario_stats.backend.mean):>10}) "
            f"[Wall: {unit_formatter.format(scenario_stats.wall.mean):>10}]"
        )


def main() -> None:
    """parse command-line arguments and orchestrate benchmark execution."""
    args = parse_args()

    build_base = args.build_base.resolve()
    report_dir = args.report_dir.resolve()
    traces_dir = report_dir / "traces"
    plots_dir = report_dir / "plots"

    compiler_info = get_compiler_identifier(args.clang)
    suite = BenchmarkSuite(
        cmake_binary_path=args.cmake,
        clang_binary_path=args.clang,
        out_base_dir=build_base,
        report_dir=report_dir,
        iteration_count=args.runs,
    )

    suite_banner = f"""Vulkan-Hpp C++20 modules vs headers and PCH benchmark suite (CMake instrumentation)
Compiler: {compiler_info} ({args.clang})
CMake: {args.cmake}
Iterations: {args.runs}
Configurations: {', '.join(args.configs)}
Scenarios: {', '.join(args.scenarios)}
Build base: {build_base}
Report directory: {report_dir}
"""
    print(suite_banner)

    # Preserve canonical ordering while allowing callers to run a focused subset.
    active_scenarios = [
        scenario
        for scenario in SCENARIO_DEFINITIONS
        if scenario[0] in args.scenarios
    ]

    if args.import_log:
        # Imports intentionally skip configuration and builds so historical runs remain reproducible.
        print(f"Importing run data from log: {args.import_log}")
        suite.import_log(args.import_log)
    elif args.import_data:
        print(f"Importing run data from JSON: {args.import_data}")
        suite.import_data_json(args.import_data)
    else:
        # Fresh configuration prevents cache state from making one configuration inherit another's settings.
        for config_key in args.configs:
            configuration = suite.configs[config_key]
            if not suite.configure_build(configuration, fresh_configure=True):
                sys.exit(1)

        # Run every scenario per configuration to avoid mixing artifacts between build strategies.
        for config_key in args.configs:
            configuration = suite.configs[config_key]
            build_directory = suite.out_base_dir / configuration.out_dir_name

            for scenario_id, scenario_title in active_scenarios:
                if scenario_id == "clean":
                    scenario_runs = suite.run_benchmark_scenario(
                        configuration,
                        scenario_id,
                        scenario_title,
                        setup_fn=lambda target_directory=build_directory: suite.clean_build_dir(target_directory),
                        jobs=args.jobs,
                    )
                else:
                    touch_target_file = get_scenario_touch_file(scenario_id, configuration.name)
                    scenario_runs = suite.run_benchmark_scenario(
                        configuration,
                        scenario_id,
                        scenario_title,
                        setup_fn=touch_target_file.touch if touch_target_file else None,
                        baseline_first=True,
                        jobs=args.jobs,
                    )
                suite.results.extend(scenario_runs)

    # Persist raw samples before reduction so reports can be regenerated without rebuilding.
    data_json_path = (
        args.export_data if args.export_data else report_dir / "benchmark_data.json"
    )
    suite.export_data_json(data_json_path)
    print(f"Exported benchmark data to: {data_json_path}")

    # Trace collection follows reduction because the report consumes both statistics and retained artifacts.
    stats = suite.calculate_stats()
    suite.collect_trace_data(traces_dir)
    print(f"\nCollected trace data and Ninjatracing profiles in: {traces_dir}")

    # SVG preserves timing detail while keeping the report portable.
    plots = generate_svg_visualizations(
        stats,
        plots_dir,
        active_scenarios,
    )
    print(f"Generated SVG box-and-whisker plots in: {plots_dir} ({len(plots)} plots)")

    # The report records invocation arguments alongside measurements for later interpretation.
    report_path = report_dir / "benchmark_report.html"
    config_display_names = {
        config_key: config_object.display_name
        for config_key, config_object in suite.configs.items()
    }
    generate_html_report(
        stats,
        report_path,
        active_scenarios,
        compiler_info=compiler_info,
        cmake_path=args.cmake,
        iteration_count=args.runs,
        config_display_names=config_display_names,
        cli_arguments=sys.argv,
    )
    print(f"Benchmark report generated: {report_path}")

    # Keep the representative comparison visible in CI and terminal-only runs.
    comparisons = compare_scenarios(stats, active_scenarios)
    primary_scenario_id = (
        "touch-root-interface"
        if "touch-root-interface" in comparisons
        else (active_scenarios[0][0] if active_scenarios and active_scenarios[0][0] in comparisons else None)
    )
    if primary_scenario_id and primary_scenario_id in comparisons:
        print_primary_comparison(comparisons[primary_scenario_id])

    print_all_scenarios_summary(stats)
