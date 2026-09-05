"""data models, timing representations, and statistical structures for the benchmark suite."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
import statistics
from whenever import TimeDelta, nanoseconds


def to_delta(
    ns: float | int = 0,
    *,
    us: float | int = 0,
    ms: float | int = 0,
    s: float | int = 0,
) -> TimeDelta:
    """sum arbitrary time units into a nanosecond-resolution TimeDelta."""
    total_nanoseconds = round(ns + us * 1_000 + ms * 1_000_000 + s * 1_000_000_000)
    return nanoseconds(total_nanoseconds)


@dataclass(frozen=True)
class TimedCommandResult:
    """subprocess execution output bundled with wall clock duration."""

    returncode: int
    stdout: str
    stderr: str
    wall_time: TimeDelta


@dataclass(frozen=True)
class BuildConfig:
    """compiler, preset, and generator options for a benchmark build configuration."""

    name: str
    display_name: str
    out_dir_name: str
    cmake_flags: list[str]


@dataclass(frozen=True)
class TargetExecution:
    """single build step parsed from .ninja_log with start and end offsets."""

    start: TimeDelta
    end: TimeDelta
    duration: TimeDelta
    output: str
    command_hash: str


@dataclass(frozen=True)
class TargetTypeBreakdown:
    """target counts grouped by build phase."""

    compilations: int = 0
    scans: int = 0
    dynamic_dependencies: int = 0
    links: int = 0
    custom_commands: int = 0

    @property
    def total(self) -> int:
        return (
            self.compilations
            + self.scans
            + self.dynamic_dependencies
            + self.links
            + self.custom_commands
        )

    @classmethod
    def average(cls, breakdowns: Sequence["TargetTypeBreakdown"]) -> "TargetTypeBreakdown":
        """compute average integer target counts across a sequence of breakdowns."""
        if not breakdowns:
            return cls()
        # Round each phase independently so displayed integer categories still sum to the displayed total.
        return cls(
            compilations=round(statistics.mean([breakdown.compilations for breakdown in breakdowns])),
            scans=round(statistics.mean([breakdown.scans for breakdown in breakdowns])),
            dynamic_dependencies=round(statistics.mean([breakdown.dynamic_dependencies for breakdown in breakdowns])),
            links=round(statistics.mean([breakdown.links for breakdown in breakdowns])),
            custom_commands=round(statistics.mean([breakdown.custom_commands for breakdown in breakdowns])),
        )


@dataclass
class BuildRunResult:
    """timing measurements and trace paths collected from one build iteration."""

    config_name: str
    scenario_name: str
    run_index: int
    compiler_time: TimeDelta
    frontend_time: TimeDelta
    backend_time: TimeDelta
    wall_time: TimeDelta
    exit_code: int
    targets_built: int
    ninja_targets: list[TargetExecution] = field(default_factory=list)
    time_trace_files: list[Path] = field(default_factory=list)
    jobs: int = 0
    target_breakdown: TargetTypeBreakdown = field(default_factory=TargetTypeBreakdown)


@dataclass(frozen=True)
class MetricStats:
    """summary statistics for a duration series across benchmark samples."""

    mean: TimeDelta
    stddev: TimeDelta
    min: TimeDelta
    max: TimeDelta
    median: TimeDelta
    raw: list[TimeDelta] = field(default_factory=list)

    @classmethod
    def compute(cls, values: Sequence[TimeDelta]) -> "MetricStats":
        """calculate mean, standard deviation, extrema, and median from sample durations."""
        if not values:
            return cls.empty()
        nanoseconds_list = [value.total("nanoseconds") for value in values]
        mean_delta = to_delta(statistics.mean(nanoseconds_list))
        stddev_delta = (
            to_delta(statistics.stdev(nanoseconds_list))
            if len(nanoseconds_list) > 1
            else TimeDelta.ZERO
        )
        median_delta = to_delta(statistics.median(nanoseconds_list))
        return cls(
            mean=mean_delta,
            stddev=stddev_delta,
            min=min(values),
            max=max(values),
            median=median_delta,
            raw=list(values),
        )

    @classmethod
    def empty(cls) -> "MetricStats":
        """create a zero-initialized statistical summary when samples are absent."""
        zero_delta = TimeDelta.ZERO
        return cls(
            mean=zero_delta,
            stddev=zero_delta,
            min=zero_delta,
            max=zero_delta,
            median=zero_delta,
            raw=[],
        )


@dataclass
class ScenarioStats:
    """aggregated statistics across iterations for a specific configuration and scenario."""

    config_name: str
    scenario_name: str
    runs: int
    compiler: MetricStats
    frontend: MetricStats
    backend: MetricStats
    wall: MetricStats
    targets_mean: float
    target_breakdown: TargetTypeBreakdown = field(default_factory=TargetTypeBreakdown)
    jobs: int = 0

    @property
    def avg_compiler_time_per_target(self) -> TimeDelta:
        """calculate average compiler CPU duration per compilation target."""
        # Use compilation count when available; scans and links do not contribute to Clang CPU time.
        divisor = float(self.target_breakdown.compilations) if self.target_breakdown.compilations > 0 else (self.targets_mean if self.targets_mean > 0 else 1.0)
        return to_delta(ns=self.compiler.mean.total("nanoseconds") / divisor)

    @classmethod
    def empty(cls) -> "ScenarioStats":
        """create an empty statistical summary when a scenario was not executed."""
        zero_metric = MetricStats.empty()
        return cls(
            config_name="",
            scenario_name="",
            runs=0,
            compiler=zero_metric,
            frontend=zero_metric,
            backend=zero_metric,
            wall=zero_metric,
            targets_mean=0.0,
            target_breakdown=TargetTypeBreakdown(),
            jobs=0,
        )


@dataclass(frozen=True)
class MetricComparison:
    """ratio comparison of a duration metric across modules, precompiled headers, and headers-only."""

    modules: TimeDelta
    precompiled_headers: TimeDelta
    headers_only: TimeDelta
    speedup_versus_pch: float
    speedup_versus_headers: float

    @classmethod
    def from_stats(
        cls,
        modules_metric: MetricStats,
        pch_metric: MetricStats,
        headers_metric: MetricStats,
    ) -> "MetricComparison":
        """compute speedup multipliers relative to modules baseline duration."""
        modules_mean = modules_metric.mean
        pch_mean = pch_metric.mean
        headers_mean = headers_metric.mean

        speedup_pch = (
            pch_mean / modules_mean if modules_mean > TimeDelta.ZERO else 0.0
        )
        speedup_headers = (
            headers_mean / modules_mean if modules_mean > TimeDelta.ZERO else 0.0
        )
        return cls(
            modules=modules_mean,
            precompiled_headers=pch_mean,
            headers_only=headers_mean,
            speedup_versus_pch=speedup_pch,
            speedup_versus_headers=speedup_headers,
        )


@dataclass(frozen=True)
class ScenarioComparison:
    """structured multi-configuration comparison across compiler and wall metrics for a scenario."""

    scenario_name: str
    scenario_title: str
    compiler: MetricComparison
    wall: MetricComparison

    @classmethod
    def from_scenario_stats(
        cls,
        scenario_name: str,
        scenario_title: str,
        modules_stats: ScenarioStats,
        pch_stats: ScenarioStats,
        headers_stats: ScenarioStats,
    ) -> "ScenarioComparison":
        """construct structured comparison across compiler CPU and wall clock metrics."""
        return cls(
            scenario_name=scenario_name,
            scenario_title=scenario_title,
            compiler=MetricComparison.from_stats(
                modules_stats.compiler,
                pch_stats.compiler,
                headers_stats.compiler,
            ),
            wall=MetricComparison.from_stats(
                modules_stats.wall,
                pch_stats.wall,
                headers_stats.wall,
            ),
        )
