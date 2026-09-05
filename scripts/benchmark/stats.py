"""statistical reductions and comparison functions across benchmark samples."""

from collections.abc import Sequence
import statistics
from dominate.tags import b
from whenever import TimeDelta

from .constants import METRIC_ATTRIBUTE_MAP
from .formatters import SIUnitFormatter
from .models import (
    BuildRunResult,
    MetricStats,
    ScenarioComparison,
    ScenarioStats,
    TargetTypeBreakdown,
)


def compute_scenario_stats(
    config_name: str,
    scenario_name: str,
    runs: Sequence[BuildRunResult],
) -> ScenarioStats:
    """reduce run samples into a ScenarioStats summary by mapping MetricStats.compute across all metric fields."""
    if not runs:
        return ScenarioStats.empty()

    computed_metrics: dict[str, MetricStats] = {
        metric_name: MetricStats.compute([getattr(build_run, attribute_name) for build_run in runs])
        for metric_name, attribute_name in METRIC_ATTRIBUTE_MAP.items()
    }

    target_counts = [build_run.targets_built for build_run in runs]
    targets_mean = statistics.mean(target_counts) if target_counts else 0.0
    target_breakdown = TargetTypeBreakdown.average([build_run.target_breakdown for build_run in runs])

    return ScenarioStats(
        config_name=config_name,
        scenario_name=scenario_name,
        runs=len(runs),
        compiler=computed_metrics["compiler"],
        frontend=computed_metrics["frontend"],
        backend=computed_metrics["backend"],
        wall=computed_metrics["wall"],
        targets_mean=targets_mean,
        target_breakdown=target_breakdown,
        jobs=runs[0].jobs if runs else 0,
    )


def calculate_all_stats(
    results: Sequence[BuildRunResult],
) -> dict[tuple[str, str], ScenarioStats]:
    """partition runs by configuration and scenario, then compute statistical summaries for each group."""
    grouped_runs: dict[tuple[str, str], list[BuildRunResult]] = {}
    for build_run in results:
        grouped_runs.setdefault((build_run.config_name, build_run.scenario_name), []).append(build_run)

    return {
        (configuration_name, scenario_name): compute_scenario_stats(
            configuration_name, scenario_name, scenario_runs
        )
        for (configuration_name, scenario_name), scenario_runs in grouped_runs.items()
    }


def compare_scenarios(
    stats: dict[tuple[str, str], ScenarioStats],
    scenarios: Sequence[tuple[str, str]],
) -> dict[str, ScenarioComparison]:
    """construct structured ScenarioComparison objects for scenarios containing all three build variants."""
    comparisons: dict[str, ScenarioComparison] = {}
    for scenario_id, scenario_title in scenarios:
        modules_key = ("modules", scenario_id)
        pch_key = ("pch", scenario_id)
        headers_key = ("headers", scenario_id)

        if modules_key not in stats or pch_key not in stats or headers_key not in stats:
            continue

        comparisons[scenario_id] = ScenarioComparison.from_scenario_stats(
            scenario_name=scenario_id,
            scenario_title=scenario_title,
            modules_stats=stats[modules_key],
            pch_stats=stats[pch_key],
            headers_stats=stats[headers_key],
        )
    return comparisons


def make_comparison_row(
    label: str,
    modules_stats: ScenarioStats,
    pch_stats: ScenarioStats,
    headers_stats: ScenarioStats,
    metric_name: str = "compiler",
    formatter: SIUnitFormatter | None = None,
) -> list[str]:
    """format comparison table row comparing module speedup multipliers against precompiled headers and header-only builds."""
    unit_formatter = formatter or SIUnitFormatter(unit="s", precision=3)
    modules_metric: MetricStats = getattr(modules_stats, metric_name)
    pch_metric: MetricStats = getattr(pch_stats, metric_name)
    headers_metric: MetricStats = getattr(headers_stats, metric_name)

    modules_mean = modules_metric.mean
    pch_mean = pch_metric.mean
    headers_mean = headers_metric.mean

    relative_pch = pch_mean / modules_mean if modules_mean > TimeDelta.ZERO else 0.0
    relative_headers = headers_mean / modules_mean if modules_mean > TimeDelta.ZERO else 0.0

    return [
        label,
        unit_formatter.format_value_with_deviation(modules_mean, modules_metric.stddev),
        unit_formatter.format_value_with_deviation(pch_mean, pch_metric.stddev),
        unit_formatter.format_value_with_deviation(headers_mean, headers_metric.stddev),
        b(f"{relative_pch:.2f}×").render(pretty=False),
        b(f"{relative_headers:.2f}×").render(pretty=False),
    ]
