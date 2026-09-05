"""html report generator using dominate and tabulate to construct benchmark reports."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any
from dominate.document import document as dominate_document
from dominate.tags import b, code, h1, h2, h3, img, li, meta, p, style, ul
from dominate.util import raw
from tabulate import tabulate

from .formatters import SIUnitFormatter
from .models import ScenarioStats
from .stats import compare_scenarios, make_comparison_row

# configure dominate inline tags so pretty-printing does not insert newlines around inline elements
code.is_inline = True
b.is_inline = True


SCENARIO_RATIONALES: dict[str, str] = {
    "clean": "Establishes the full-build baseline, including every module interface, sample, and link target.",
    "touch-root-interface": "Models a change to Vulkan-Hpp's public API boundary and its broadest incremental dependency fan-out.",
    "touch-core-header": "Measures the effect of changing macros shared by the core public headers and module interface.",
    "touch-intermediate-interface": "Models a shared sample utility change that affects an intermediate layer, rather than the Vulkan-Hpp core.",
    "touch-cpp": "Provides the narrow incremental baseline for a change confined to one leaf translation unit.",
}


def get_scenario_components(scenario_id: str, default_title: str = "") -> list[Any]:
    """return list of string and dominate code elements representing the scenario title."""
    if scenario_id == "touch-root-interface":
        return ["Core interface rebuild (", code("vulkan.cppm"), "/", code("vulkan.hpp"), ")"]
    if scenario_id == "touch-core-header":
        return ["Core macros rebuild (", code("vulkan_hpp_macros.hpp"), ")"]
    if scenario_id == "touch-intermediate-interface":
        return ["Intermediate interface rebuild (", code("utils.cppm"), "/", code("utils.hpp"), ")"]
    if scenario_id == "touch-cpp":
        return ["Leaf translation unit rebuild (", code("RayTracing.cpp"), ")"]
    if scenario_id == "clean":
        return ["Clean build (all targets)"]
    return [default_title or scenario_id]


def render_scenario_html(scenario_id: str, default_title: str = "") -> str:
    """render scenario title using dominate tags for inclusion in html tables."""
    components = get_scenario_components(scenario_id, default_title)
    return "".join(
        item.render(pretty=False) if hasattr(item, "render") else str(item)
        for item in components
    )


def render_table_html(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> str:
    """render tabular data to an html table string using tabulate."""
    return tabulate(rows, headers=headers, tablefmt="unsafehtml")


def generate_html_report(
    stats: dict[tuple[str, str], ScenarioStats],
    output_report_file: Path,
    scenarios: Sequence[tuple[str, str]],
    compiler_info: str,
    cmake_path: str,
    iteration_count: int,
    config_display_names: dict[str, str],
    comparison_scenario: str = "touch-root-interface",
    formatter: SIUnitFormatter | None = None,
    cli_arguments: Sequence[str] | None = None,
) -> None:
    """generate an html benchmark report using dominate and tabulate."""
    unit_formatter = formatter or SIUnitFormatter(unit="s", precision=3)
    active_scenario_ids = [scenario_id for scenario_id, _ in scenarios]
    primary_scenario_id = (
        comparison_scenario
        if comparison_scenario in active_scenario_ids
        else (active_scenario_ids[0] if active_scenario_ids else "")
    )
    primary_scenario_title = dict(scenarios).get(primary_scenario_id, primary_scenario_id)

    scenario_comparisons = compare_scenarios(stats, scenarios)
    primary_comparison = scenario_comparisons.get(primary_scenario_id)

    modules_primary_stats = stats.get(("modules", primary_scenario_id))
    pch_primary_stats = stats.get(("pch", primary_scenario_id))
    headers_primary_stats = stats.get(("headers", primary_scenario_id))

    headers_compiler_table = [
        "Scenario",
        "Modules (s)",
        "Precompiled headers (s)",
        "Headers only (s)",
        "Speedup vs PCH",
        "Speedup vs headers",
    ]
    rows_compiler_table = [
        make_comparison_row(
            render_scenario_html(scenario_id, scenario_title),
            stats[("modules", scenario_id)],
            stats[("pch", scenario_id)],
            stats[("headers", scenario_id)],
            metric_name="compiler",
            formatter=unit_formatter,
        )
        for scenario_id, scenario_title in scenarios
        if ("modules", scenario_id) in stats
        and ("pch", scenario_id) in stats
        and ("headers", scenario_id) in stats
    ]
    compiler_html = render_table_html(rows_compiler_table, headers_compiler_table)

    headers_wall_table = [
        "Scenario",
        "Modules (wall) (s)",
        "Precompiled headers (wall) (s)",
        "Headers only (wall) (s)",
        "Speedup vs PCH",
        "Speedup vs headers",
    ]
    rows_wall_table = [
        make_comparison_row(
            render_scenario_html(scenario_id, scenario_title),
            stats[("modules", scenario_id)],
            stats[("pch", scenario_id)],
            stats[("headers", scenario_id)],
            metric_name="wall",
            formatter=unit_formatter,
        )
        for scenario_id, scenario_title in scenarios
        if ("modules", scenario_id) in stats
        and ("pch", scenario_id) in stats
        and ("headers", scenario_id) in stats
    ]
    wall_html = render_table_html(rows_wall_table, headers_wall_table)

    # The exemplar is optional because focused runs may omit a complete three-way comparison.
    summary_html: str | None = None
    if primary_comparison and modules_primary_stats and pch_primary_stats and headers_primary_stats:
        compiler_comparison = primary_comparison.compiler
        wall_comparison = primary_comparison.wall
        summary_headers = [
            "Metric",
            "Modules (s)",
            "Precompiled headers (s)",
            "Headers only (s)",
            "Speedup vs PCH",
            "Speedup vs headers",
        ]
        summary_rows = [
            [
                "Compiler CPU time",
                unit_formatter.format_value_with_deviation(compiler_comparison.modules, modules_primary_stats.compiler.stddev),
                unit_formatter.format_value_with_deviation(compiler_comparison.precompiled_headers, pch_primary_stats.compiler.stddev),
                unit_formatter.format_value_with_deviation(compiler_comparison.headers_only, headers_primary_stats.compiler.stddev),
                b(f"{compiler_comparison.speedup_versus_pch:.2f}×").render(pretty=False),
                b(f"{compiler_comparison.speedup_versus_headers:.2f}×").render(pretty=False),
            ],
            [
                "Wall-clock build time",
                unit_formatter.format_value_with_deviation(wall_comparison.modules, modules_primary_stats.wall.stddev),
                unit_formatter.format_value_with_deviation(wall_comparison.precompiled_headers, pch_primary_stats.wall.stddev),
                unit_formatter.format_value_with_deviation(wall_comparison.headers_only, headers_primary_stats.wall.stddev),
                b(f"{wall_comparison.speedup_versus_pch:.2f}×").render(pretty=False),
                b(f"{wall_comparison.speedup_versus_headers:.2f}×").render(pretty=False),
            ],
        ]
        summary_html = render_table_html(summary_rows, summary_headers)

    headers_target_table = [
        "Configuration",
        "Compilations",
        "Scans",
        "Dyndep",
        "Links",
        "Custom commands",
        "Total targets",
    ]
    # Render phase counts separately so module graph maintenance remains visible beside compilation work.
    target_html_by_scenario: dict[str, str] = {}
    for scenario_id, _ in scenarios:
        rows_target = [
            [
                config_display_names.get(configuration_id, configuration_id),
                str(scenario_stats.target_breakdown.compilations),
                str(scenario_stats.target_breakdown.scans),
                str(scenario_stats.target_breakdown.dynamic_dependencies),
                str(scenario_stats.target_breakdown.links),
                str(scenario_stats.target_breakdown.custom_commands),
                str(scenario_stats.target_breakdown.total or int(scenario_stats.targets_mean)),
            ]
            for configuration_id in ["modules", "pch", "headers"]
            if (scenario_stats := stats.get((configuration_id, scenario_id))) is not None
        ]
        if rows_target:
            target_html_by_scenario[scenario_id] = render_table_html(rows_target, headers_target_table)

    headers_phase_table = [
        "Configuration",
        "Compiler CPU (s)",
        "Frontend (s)",
        "Backend (s)",
        "Wall time (s)",
        "Compiles",
        "Scans",
        "Dyndep",
        "Links",
        "Custom commands",
        "Total targets",
        "Avg time / target (s)",
    ]
    # Combine timings and edge counts only after statistics have been reduced across equal run sets.
    phase_html_by_scenario: dict[str, str] = {}
    for scenario_id, _ in scenarios:
        rows_phase = [
            [
                config_display_names.get(configuration_id, configuration_id),
                unit_formatter.format_value_with_deviation(scenario_stats.compiler.mean, scenario_stats.compiler.stddev),
                unit_formatter.format_value(scenario_stats.frontend.mean),
                unit_formatter.format_value(scenario_stats.backend.mean),
                unit_formatter.format_value(scenario_stats.wall.mean),
                str(scenario_stats.target_breakdown.compilations),
                str(scenario_stats.target_breakdown.scans),
                str(scenario_stats.target_breakdown.dynamic_dependencies),
                str(scenario_stats.target_breakdown.links),
                str(scenario_stats.target_breakdown.custom_commands),
                str(scenario_stats.target_breakdown.total or int(scenario_stats.targets_mean)),
                unit_formatter.format_value(scenario_stats.avg_compiler_time_per_target),
            ]
            for configuration_id in ["modules", "pch", "headers"]
            if (scenario_stats := stats.get((configuration_id, scenario_id))) is not None
        ]
        if rows_phase:
            phase_html_by_scenario[scenario_id] = render_table_html(rows_phase, headers_phase_table)

    document = dominate_document(title="Benchmark report: C++20 modules vs headers and PCH")
    document.set_attribute("lang", "en")
    with document.head:
        meta(name="viewport", content="width=device-width, initial-scale=1")
        style(
            raw(
                """
            body {
                max-width: 65em;
                margin: 40px auto;
                padding: 0 20px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                line-height: 1.6;
                font-size: 16px;
                color: #222;
                background: #fff;
            }
            h1, h2, h3 {
                line-height: 1.2;
                margin-top: 1.5em;
            }
            table {
                border-collapse: collapse;
                width: 100%;
                margin: 1.5em 0;
            }
            th, td {
                border: 1px solid #ccc;
                padding: 6px 10px;
            }
            th {
                background: #f4f4f4;
            }
            code {
                font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
                font-size: 0.9em;
                background: #f4f4f4;
                padding: 2px 5px;
                border-radius: 3px;
            }
            img {
                max-width: 100%;
                height: auto;
                display: block;
                margin: 1.5em 0;
                border: 1px solid #ccc;
                border-radius: 4px;
            }
            @media (prefers-color-scheme: dark) {
                body {
                    color: #c9d1d9;
                    background: #0d1117;
                }
                th, td {
                    border-color: #30363d;
                }
                th {
                    background: #161b22;
                }
                code {
                    background: #161b22;
                }
                img {
                    border-color: #30363d;
                }
            }
            """
            )
        )

    with document:
        h1("Benchmark report: C++20 modules vs headers and PCH")

        h2("Environment and command")
        with ul():
            li("Compiler: ", code(compiler_info))
            li(code("cmake"), f": {cmake_path} (v1.1 instrumentation)")
            li("CLI arguments: ", code(" ".join(cli_arguments) if cli_arguments else "not recorded"))

        h2("Methodology")
        p(
            f"""Each configuration runs every selected scenario {iteration_count} times. For incremental scenarios, an initial build establishes the baseline, then the named source file is touched before each measured build. The clean scenario removes build outputs before every measured build. Each result reports compiler CPU time from """,
            code("cmake"),
            """ instrumentation and wall-clock time for the full """,
            code("cmake --build"),
            """ invocation. Compiler phase timings are collected from """,
            code("trace/trace-*.json"),
            """ and """,
            code("clang -ftime-trace"),
            """. Modules, precompiled headers, and textual headers use the same source tree and build targets so their rebuild work can be compared directly.""",
        )

        h3("Scenarios")
        with ul():
            for scenario_id, scenario_title in scenarios:
                li(
                    *get_scenario_components(scenario_id, scenario_title),
                    ": ",
                    SCENARIO_RATIONALES.get(scenario_id, "Measures the selected rebuild scope."),
                )

        if summary_html is not None:
            h2("Highlighted scenario")
            p(
                "The primary comparison is ",
                *get_scenario_components(primary_scenario_id, primary_scenario_title),
                ". It represents a broadly visible API change, making it a useful concrete example of how the three build approaches behave when a shared interface changes.",
            )
            raw(summary_html)

        h2("Target breakdown by type")
        p(
            """Build steps categorized by phase across Ninja targets.
C++20 module builds include dependency scanning (""",
            code("clang-scan-deps"),
            """) and dynamic dependency generation (""",
            code("cmake_ninja_dyndep"),
            """), while precompiled header and header builds execute compilation and link steps directly.""",
        )
        for scenario_id, scenario_title in scenarios:
            if scenario_id in target_html_by_scenario:
                h3(*get_scenario_components(scenario_id, scenario_title))
                raw(target_html_by_scenario[scenario_id])

        h2("Compiler CPU time")
        p(
            """Direct compiler CPU time represents time spent inside """,
            code("clang"),
            """ compiler frontend and backend, isolating compilation speed from disk caching and process fork overhead.""",
        )

        if primary_scenario_id == "touch-root-interface":
            p(
                """Altering the root interface requires regenerating the built module interface (BMI) once for """,
                code("vulkan.cppm"),
                """, after which dependent translation units load cached interface definitions without re-parsing.
In contrast, header builds force a complete AST re-parse across every translation unit.""",
            )

        raw(compiler_html)

        img(src="plots/scenarios_compiler_time_box_plot.svg", alt="Compiler CPU time across scenarios")

        h2("Wall-clock build time")
        p(
            """Wall-clock build duration across end-to-end execution of """,
            code("cmake --build"),
            """.
Measures user-perceived completion turnaround time under parallel build scheduling.""",
        )

        if primary_scenario_id == "touch-root-interface":
            p(
                """Wall-clock duration reflects end-to-end build latency under parallel Ninja scheduling.
Downstream translation units compile concurrently as soon as """,
                code("vulkan.pcm"),
                """ finishes emitting, whereas header builds saturate compiler workers with redundant tokenization and macro expansion on every thread.""",
            )

        raw(wall_html)

        img(src="plots/scenarios_wall_time_box_plot.svg", alt="Wall-clock build time across scenarios")

        h2("Compiler phase breakdown")
        p(
            code("clang -ftime-trace"),
            """ separates frontend parse (source lexing, macro expansion, header parsing, module BMI loading, template instantiation) and backend codegen (LLVM optimization passes, instruction selection, code generation).""",
        )

        for scenario_id, scenario_title in scenarios:
            if scenario_id in phase_html_by_scenario:
                h3(*get_scenario_components(scenario_id, scenario_title))
                raw(phase_html_by_scenario[scenario_id])

        img(src="plots/compiler_phase_breakdown_bar_plot.svg", alt="Compiler phase breakdown: frontend parse vs backend codegen")

    output_report_file.parent.mkdir(parents=True, exist_ok=True)
    rendered_html = "\n".join(line.rstrip() for line in document.render().splitlines()) + "\n"
    output_report_file.write_text(rendered_html, encoding="utf-8")


# alias for backwards compatibility
generate_markdown_report = generate_html_report
