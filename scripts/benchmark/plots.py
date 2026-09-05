"""visualization generators for grouped boxplots and compiler phase breakdowns in SVG format."""

from collections.abc import Sequence
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "matplotlib"))
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["font.family"] = "sans-serif"
matplotlib.rcParams["font.sans-serif"] = [
    "-apple-system",
    "BlinkMacSystemFont",
    "Segoe UI",
    "Roboto",
    "Helvetica",
    "Arial",
    "sans-serif",
]
matplotlib.rcParams["text.color"] = "#222222"
matplotlib.rcParams["axes.labelcolor"] = "#222222"
matplotlib.rcParams["xtick.color"] = "#222222"
matplotlib.rcParams["ytick.color"] = "#222222"
matplotlib.rcParams["axes.edgecolor"] = "#cccccc"
matplotlib.rcParams["grid.color"] = "#e5e7eb"
matplotlib.rcParams["figure.facecolor"] = "#ffffff"
matplotlib.rcParams["axes.facecolor"] = "#ffffff"
matplotlib.rcParams["legend.facecolor"] = "#ffffff"
matplotlib.rcParams["legend.edgecolor"] = "#cccccc"
import matplotlib.pyplot as plt

from .constants import CONFIGURATION_PALETTE
from .models import ScenarioStats


def embed_svg_theme_css(svg_path: Path) -> None:
    """embed stylesheet inside svg file for responsive font inheritance and dark mode styling."""
    svg_stylesheet = """
  <style type="text/css">
    text {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
    }
    @media (prefers-color-scheme: dark) {
      #figure_1 > #patch_1 > path,
      #axes_1 > #patch_2 > path {
        fill: #0d1117 !important;
      }
      text {
        fill: #c9d1d9 !important;
      }
      #axes_1 > [id^="patch_"] > path {
        stroke: #30363d !important;
      }
      [id^="xtick_"] path, [id^="ytick_"] path,
      [id^="xtick_"] use, [id^="ytick_"] use {
        stroke: #30363d !important;
      }
      [id^="ytick_"] [id^="line2d_"]:first-child > path {
        stroke: #21262d !important;
      }
      #legend_1 > [id^="patch_"]:first-child > path {
        fill: #161b22 !important;
        stroke: #30363d !important;
      }
    }
  </style>
"""
    # Matplotlib emits fixed SVG colors; inject overrides so standalone report assets honor dark mode.
    file_content = svg_path.read_text(encoding="utf-8")
    if "<defs>" in file_content:
        file_content = file_content.replace("<defs>", "<defs>" + svg_stylesheet, 1)
    else:
        file_content = file_content.replace("<svg ", "<svg>" + svg_stylesheet, 1)
    svg_path.write_text(file_content, encoding="utf-8")


def render_grouped_boxplot(
    data_by_configuration: list[tuple[str, str, list[list[float]]]],
    x_labels: Sequence[str],
    x_axis_title: str,
    y_axis_title: str,
    plot_title: str,
    output_path: Path,
    box_group_width: float = 0.25,
    label_rotation: float | None = None,
    logarithmic_y_axis: bool = False,
) -> None:
    """draw a grouped boxplot comparing configurations side-by-side across scenarios."""
    x_indices = list(range(len(x_labels)))
    figure, axis = plt.subplots(figsize=(13, 6.5))
    legend_boxes = []
    legend_labels = []
    configuration_count = len(data_by_configuration)
    box_width = 0.4 if configuration_count == 1 else box_group_width * 0.82

    for index, (configuration_key, display_label, series_data) in enumerate(data_by_configuration):
        horizontal_offset = (
            0.0
            if configuration_count == 1
            else (index - (configuration_count - 1) / 2.0) * box_group_width
        )
        positions = [x_position + horizontal_offset for x_position in x_indices]
        colors = CONFIGURATION_PALETTE.get(
            configuration_key,
            {"face": "#6b728040", "edge": "#374151"},
        )
        boxplot_result = axis.boxplot(
            series_data,
            positions=positions,
            widths=box_width,
            patch_artist=True,
            boxprops=dict(facecolor=colors["face"], edgecolor=colors["edge"], linewidth=1.5),
            medianprops=dict(color=colors["edge"], linewidth=2.5),
            whiskerprops=dict(color=colors["edge"], linewidth=1.5),
            capprops=dict(color=colors["edge"], linewidth=1.5),
            flierprops=dict(
                marker="o",
                markerfacecolor=colors["edge"],
                markeredgecolor="white",
                markersize=5,
            ),
        )
        legend_boxes.append(boxplot_result["boxes"][0])
        legend_labels.append(display_label)

    if label_rotation is None:
        label_rotation = 25.0 if any(len(label) > 12 for label in x_labels) else 0.0

    axis.set_xticks(x_indices)
    if label_rotation != 0.0:
        axis.set_xticklabels(
            x_labels,
            fontsize=9.5,
            fontweight="bold",
            rotation=label_rotation,
            ha="right",
            rotation_mode="anchor",
        )
    else:
        axis.set_xticklabels(x_labels, fontsize=10, fontweight="bold")

    if x_axis_title:
        axis.set_xlabel(x_axis_title, fontsize=11, fontweight="bold")
    axis.set_ylabel(y_axis_title, fontsize=11, fontweight="bold")
    if logarithmic_y_axis:
        # Rebuild costs span orders of magnitude; log scale keeps leaf and full builds readable together.
        axis.set_yscale("log")
    axis.set_title(plot_title, fontsize=13, fontweight="bold")
    axis.legend(legend_boxes, legend_labels, loc="upper right", framealpha=0.95)
    axis.grid(True, linestyle="--", alpha=0.5, axis="y")

    figure.savefig(output_path, format="svg", bbox_inches="tight")
    plt.close(figure)
    embed_svg_theme_css(output_path)


def generate_svg_visualizations(
    stats: dict[tuple[str, str], ScenarioStats],
    plots_directory: Path,
    scenarios: Sequence[tuple[str, str]],
) -> dict[str, Path]:
    """render vector SVG charts for compiler CPU times, wall times, and frontend vs backend phases."""
    plots_directory.mkdir(parents=True, exist_ok=True)
    generated_plots: dict[str, Path] = {}

    configuration_keys = ["modules", "pch", "headers"]
    configuration_labels = ["C++20 modules", "Precompiled headers", "Headers only"]
    scenario_labels = [scenario_title for _, scenario_title in scenarios]

    def extract_metric_series(metric_name: str) -> list[tuple[str, str, list[list[float]]]]:
        # Keep an empty scenario visible as zero rather than shifting configuration groups across the x-axis.
        return [
            (
                configuration_key,
                display_label,
                [
                    [
                        duration.total("seconds")
                        for duration in getattr(
                            stats.get((configuration_key, scenario_id), ScenarioStats.empty()),
                            metric_name,
                        ).raw
                    ]
                    or [0.0]
                    for scenario_id, _ in scenarios
                ],
            )
            for configuration_key, display_label in zip(configuration_keys, configuration_labels)
        ]

    # 1. compiler CPU time boxplot
    compiler_plot_path = plots_directory / "scenarios_compiler_time_box_plot.svg"
    render_grouped_boxplot(
        extract_metric_series("compiler"),
        scenario_labels,
        "",
        "Compiler CPU time (s)",
        "Compiler CPU time across scenarios",
        compiler_plot_path,
        label_rotation=25.0,
        logarithmic_y_axis=True,
    )
    generated_plots["scenarios_compiler"] = compiler_plot_path

    # 2. wall-clock build time boxplot
    wall_plot_path = plots_directory / "scenarios_wall_time_box_plot.svg"
    render_grouped_boxplot(
        extract_metric_series("wall"),
        scenario_labels,
        "",
        "Wall-clock time (s)",
        "Wall-clock build time across scenarios",
        wall_plot_path,
        label_rotation=25.0,
    )
    generated_plots["scenarios_wall"] = wall_plot_path

    # Clean builds expose total frontend/backend cost without incremental dependency fan-out.
    clean_stats = {
        configuration_key: stats.get((configuration_key, "clean"))
        for configuration_key in configuration_keys
    }
    frontend_times = [
        scenario_stats.frontend.mean.total("seconds") if scenario_stats else 0.0
        for scenario_stats in clean_stats.values()
    ]
    backend_times = [
        scenario_stats.backend.mean.total("seconds") if scenario_stats else 0.0
        for scenario_stats in clean_stats.values()
    ]

    x_bar_positions = list(range(len(configuration_labels)))
    figure, axis = plt.subplots(figsize=(9.5, 5.5))
    axis.bar(
        x_bar_positions,
        frontend_times,
        width=0.45,
        label="Frontend",
        color="#3b82f6",
        edgecolor="#1d4ed8",
        linewidth=1.2,
    )
    axis.bar(
        x_bar_positions,
        backend_times,
        width=0.45,
        bottom=frontend_times,
        label="Backend",
        color="#10b981",
        edgecolor="#047857",
        linewidth=1.2,
    )

    for index, (frontend_time, backend_time) in enumerate(zip(frontend_times, backend_times)):
        total_time = frontend_time + backend_time
        if total_time > 0:
            axis.text(
                index,
                total_time + (max(frontend_times + backend_times) * 0.02),
                f"{total_time:.2f}s",
                ha="center",
                va="bottom",
                fontweight="bold",
                fontsize=10.5,
            )

    axis.set_xticks(x_bar_positions)
    axis.set_xticklabels(configuration_labels, fontsize=11, fontweight="bold")
    axis.set_ylabel("Compiler CPU time (s)", fontsize=11, fontweight="bold")
    axis.set_title("Compiler phase breakdown: frontend parse vs backend codegen", fontsize=13, fontweight="bold")
    axis.legend(loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
    axis.grid(True, linestyle="--", alpha=0.5, axis="y")

    phase_plot_path = plots_directory / "compiler_phase_breakdown_bar_plot.svg"
    figure.savefig(phase_plot_path, format="svg", bbox_inches="tight")
    plt.close(figure)
    embed_svg_theme_css(phase_plot_path)
    generated_plots["phase_breakdown"] = phase_plot_path

    return generated_plots
