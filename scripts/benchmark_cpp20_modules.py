#!/usr/bin/env python3
"""Benchmark C++20 named modules vs header-only builds using CMake instrumentation and ftime-trace.

This suite evaluates compilation performance and scalability of C++20 named
modules compared against header-only and precompiled header (PCH) builds
within Vulkan-Hpp.

Mechanisms:
1. CMake instrumentation API (v1.1):
   Project-level instrumentation is activated via cmake_instrumentation() in CMakeLists.txt.
   Build commands run via 'cmake --build', which triggers the postCMakeBuild hook
   to generate trace and index artifacts without daemon races.
2. Clang -ftime-trace phase profiling:
   Compiler timing data is extracted from Clang JSON profiles, separating frontend parse
   (AST generation, BMI import, template instantiation) from backend codegen
   (LLVM optimization passes and code generation).
3. Thread schedule reconstruction via Ninjatracing:
   ninjatracing merges .ninja_log timelines with Clang -ftime-trace slices, enabling
   waterfall and flamegraph analysis in Speedscope and Perfetto.
4. Statistical sampling and distribution analysis:
   Multiple runs (default: 10) are sampled per scenario, generating box-and-whisker distributions
   to isolate compiler efficiency gains from file system caching and process noise.
"""

# Standard library
import argparse
from collections.abc import Callable
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time
from typing import Any

# Third-party
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from tabulate import tabulate
from whenever import TimeDelta, nanoseconds

# Local application
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ninjatracing


ROOT_DIR: Path = Path(__file__).resolve().parent.parent
DEFAULT_CMAKE: str = shutil.which("cmake") or "cmake"
DEFAULT_CLANG: str = shutil.which("clang++") or "clang++"


def to_delta(ns: float | int = 0, *, us: float | int = 0, ms: float | int = 0, s: float | int = 0) -> TimeDelta:
    """Convert time units to TimeDelta."""
    return nanoseconds(round(ns + us * 1_000 + ms * 1_000_000 + s * 1_000_000_000))


def compute_delta_stats(deltas: list[TimeDelta]) -> tuple[TimeDelta, TimeDelta, TimeDelta]:
    """Calculate mean, standard deviation, and median for TimeDeltas."""
    if not deltas:
        return TimeDelta.ZERO, TimeDelta.ZERO, TimeDelta.ZERO
    nanos = [d.total("nanoseconds") for d in deltas]
    mean_d = to_delta(statistics.mean(nanos))
    std_d = to_delta(statistics.stdev(nanos)) if len(nanos) > 1 else TimeDelta.ZERO
    med_d = to_delta(statistics.median(nanos))
    return mean_d, std_d, med_d


@dataclass
class BuildConfig:
    """CMake compiler and generator flags for a benchmark configuration."""

    name: str
    display_name: str
    out_dir_name: str
    cmake_flags: list[str]


@dataclass
class TargetExecution:
    """Build step parsed from Ninja logs."""

    start: TimeDelta
    end: TimeDelta
    duration: TimeDelta
    output: str
    command_hash: str


@dataclass
class BuildRunResult:
    """Instrumentation measurements and process resource usage for a run."""

    config_name: str
    scenario_name: str
    run_index: int
    compiler_time: TimeDelta
    frontend_time: TimeDelta
    backend_time: TimeDelta
    wall_time: TimeDelta
    user_time: TimeDelta
    sys_time: TimeDelta
    exit_code: int
    targets_built: int
    ninja_targets: list[TargetExecution] = field(default_factory=list)
    time_trace_files: list[Path] = field(default_factory=list)
    jobs: int = 0


@dataclass
class ScenarioStats:
    """Statistical summary for a configuration and scenario."""

    config_name: str
    scenario_name: str
    runs: int
    compiler_mean: TimeDelta
    compiler_stddev: TimeDelta
    compiler_min: TimeDelta
    compiler_max: TimeDelta
    compiler_median: TimeDelta
    frontend_mean: TimeDelta
    frontend_stddev: TimeDelta
    backend_mean: TimeDelta
    backend_stddev: TimeDelta
    wall_mean: TimeDelta
    wall_stddev: TimeDelta
    wall_min: TimeDelta
    wall_max: TimeDelta
    wall_median: TimeDelta
    user_mean: TimeDelta
    sys_mean: TimeDelta
    targets_mean: float
    raw_compiler_times: list[TimeDelta] = field(default_factory=list)
    raw_wall_times: list[TimeDelta] = field(default_factory=list)
    raw_frontend_times: list[TimeDelta] = field(default_factory=list)
    raw_backend_times: list[TimeDelta] = field(default_factory=list)
    jobs: int = 0

    @classmethod
    def empty(cls) -> "ScenarioStats":
        """Return empty statistical summary."""
        return cls(
            config_name="",
            scenario_name="",
            runs=0,
            compiler_mean=TimeDelta.ZERO,
            compiler_stddev=TimeDelta.ZERO,
            compiler_min=TimeDelta.ZERO,
            compiler_max=TimeDelta.ZERO,
            compiler_median=TimeDelta.ZERO,
            frontend_mean=TimeDelta.ZERO,
            frontend_stddev=TimeDelta.ZERO,
            backend_mean=TimeDelta.ZERO,
            backend_stddev=TimeDelta.ZERO,
            wall_mean=TimeDelta.ZERO,
            wall_stddev=TimeDelta.ZERO,
            wall_min=TimeDelta.ZERO,
            wall_max=TimeDelta.ZERO,
            wall_median=TimeDelta.ZERO,
            user_mean=TimeDelta.ZERO,
            sys_mean=TimeDelta.ZERO,
            targets_mean=0.0,
        )


def get_compiler_identifier(compiler_path: str) -> str:
    """Query compiler version identifier."""
    try:
        res = subprocess.run([compiler_path, "--version"], capture_output=True, text=True, check=True)
        return res.stdout.strip().split("\n")[0]
    except Exception:
        return compiler_path


def parse_ninja_build_log(log_path: Path) -> list[TargetExecution]:
    """Parse Ninja build log into TargetExecution records."""
    if not log_path.is_file():
        return []
    targets: list[TargetExecution] = []
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            try:
                start = to_delta(ms=int(parts[0]))
                end = to_delta(ms=int(parts[1]))
                targets.append(
                    TargetExecution(
                        start=start,
                        end=end,
                        duration=max(TimeDelta.ZERO, end - start),
                        output=parts[3],
                        command_hash=parts[4],
                    )
                )
            except ValueError:
                continue
    return targets


def parse_clang_ftime_trace(trace_path: Path) -> tuple[TimeDelta, TimeDelta, TimeDelta]:
    """Parse Clang -ftime-trace profile into compiler, frontend, and backend TimeDeltas."""
    if not trace_path.is_file():
        return TimeDelta.ZERO, TimeDelta.ZERO, TimeDelta.ZERO

    try:
        with open(trace_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return TimeDelta.ZERO, TimeDelta.ZERO, TimeDelta.ZERO

    if not isinstance(data, dict):
        return TimeDelta.ZERO, TimeDelta.ZERO, TimeDelta.ZERO

    events = data.get("traceEvents", [])
    compiler_us = 0.0
    frontend_us = 0.0
    backend_us = 0.0

    for ev in events:
        if not isinstance(ev, dict) or ev.get("ph") != "X":
            continue
        dur = ev.get("dur", 0)
        if not isinstance(dur, (int, float)):
            continue
        name = ev.get("name")
        if name == "ExecuteCompiler":
            compiler_us += float(dur)
        elif name == "Frontend":
            frontend_us += float(dur)
        elif name == "Backend":
            backend_us += float(dur)

    return to_delta(us=compiler_us), to_delta(us=frontend_us), to_delta(us=backend_us)


def find_latest_cmake_trace(trace_dir: Path) -> Path | None:
    """Locate most recent trace-*.json file in trace directory."""
    if not trace_dir.is_dir():
        return None
    candidates = [p for p in trace_dir.glob("trace-*.json") if p.stat().st_size > 10]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime_ns)


def resolve_event_trace_path(args: dict[str, Any], data_dir: Path, build_dir: Path) -> Path | None:
    """Find Clang -ftime-trace file for an instrumented compile event."""
    if "traceFile" in args and args["traceFile"]:
        candidate = data_dir / args["traceFile"]
        if candidate.is_file():
            return candidate

    outputs = args.get("outputs", [])
    if outputs:
        work_dir = Path(args.get("workingDir", build_dir))
        candidate = work_dir / Path(outputs[0]).with_suffix(".json")
        if candidate.is_file():
            return candidate
    return None


def parse_cmake_instrumentation_trace(
    build_dir: Path,
) -> tuple[TimeDelta, TimeDelta, TimeDelta, int, list[Path]]:
    """Parse CMake instrumentation trace and Clang -ftime-trace profiles."""
    trace_dir = build_dir / ".cmake" / "instrumentation" / "v1" / "data" / "trace"
    latest_trace = find_latest_cmake_trace(trace_dir)
    if not latest_trace:
        return TimeDelta.ZERO, TimeDelta.ZERO, TimeDelta.ZERO, 0, []

    try:
        with open(latest_trace, "r", encoding="utf-8") as f:
            events = json.load(f)
    except (json.JSONDecodeError, OSError):
        return TimeDelta.ZERO, TimeDelta.ZERO, TimeDelta.ZERO, 0, []

    if not isinstance(events, list):
        return TimeDelta.ZERO, TimeDelta.ZERO, TimeDelta.ZERO, 0, []

    data_dir = build_dir / ".cmake" / "instrumentation" / "v1" / "data"
    total_compiler = TimeDelta.ZERO
    total_frontend = TimeDelta.ZERO
    total_backend = TimeDelta.ZERO
    compile_count = 0
    found_trace_files: list[Path] = []

    for ev in events:
        if not isinstance(ev, dict):
            continue
        args = ev.get("args", {})
        if not isinstance(args, dict):
            continue
        if ev.get("cat") != "compile" and args.get("role") != "compile":
            continue

        outputs = args.get("outputs", [])
        cmd = args.get("command", "")
        if "clang-scan-deps" in cmd or (outputs and outputs[0].endswith(".ddi")):
            continue

        compile_count += 1
        trace_file = resolve_event_trace_path(args, data_dir, build_dir)
        if trace_file:
            found_trace_files.append(trace_file)
            c_delta, fe_delta, be_delta = parse_clang_ftime_trace(trace_file)
            event_dur = to_delta(us=float(ev.get("dur", 0)))
            total_compiler += c_delta if c_delta > TimeDelta.ZERO else event_dur
            total_frontend += fe_delta
            total_backend += be_delta
        else:
            total_compiler += to_delta(us=float(ev.get("dur", 0)))

    return total_compiler, total_frontend, total_backend, compile_count, found_trace_files


def render_grouped_boxplot(
    data_by_config: list[tuple[str, str, list[list[float]]]],
    x_labels: list[str],
    x_axis_title: str,
    y_axis_title: str,
    plot_title: str,
    output_path: Path,
    width: float = 0.25,
    rotation: float | None = None,
) -> None:
    """Render grouped box-and-whisker plot for configurations across categories."""
    palette = {
        "modules": {"face": "#3b82f640", "edge": "#1d4ed8"},
        "pch": {"face": "#10b98140", "edge": "#047857"},
        "headers": {"face": "#ef444440", "edge": "#b91c1c"},
    }
    x_indices = np.arange(len(x_labels))
    fig, ax = plt.subplots(figsize=(13, 6.5))
    legend_boxes = []
    labels = []
    num_configs = len(data_by_config)
    box_width = 0.4 if num_configs == 1 else width * 0.82

    for idx, (cfg_key, cfg_label, config_series) in enumerate(data_by_config):
        offset = 0.0 if num_configs == 1 else (idx - (num_configs - 1) / 2.0) * width
        pos = x_indices + offset
        col = palette.get(cfg_key, {"face": "#6b728040", "edge": "#374151"})
        bp = ax.boxplot(
            config_series,
            positions=pos,
            widths=box_width,
            patch_artist=True,
            boxprops=dict(facecolor=col["face"], edgecolor=col["edge"], linewidth=1.5),
            medianprops=dict(color=col["edge"], linewidth=2.5),
            whiskerprops=dict(color=col["edge"], linewidth=1.5),
            capprops=dict(color=col["edge"], linewidth=1.5),
            flierprops=dict(marker="o", markerfacecolor=col["edge"], markeredgecolor="white", markersize=5),
        )
        legend_boxes.append(bp["boxes"][0])
        labels.append(cfg_label)

    if rotation is None:
        rotation = 25.0 if any(len(lbl) > 12 for lbl in x_labels) else 0.0

    ax.set_xticks(x_indices)
    if rotation != 0.0:
        ax.set_xticklabels(x_labels, fontsize=9.5, fontweight="bold", rotation=rotation, ha="right", rotation_mode="anchor")
    else:
        ax.set_xticklabels(x_labels, fontsize=10, fontweight="bold")
    if x_axis_title:
        ax.set_xlabel(x_axis_title, fontsize=11, fontweight="bold")
    ax.set_ylabel(y_axis_title, fontsize=11, fontweight="bold")
    ax.set_title(plot_title, fontsize=13, fontweight="bold")
    ax.legend(legend_boxes, labels, loc="upper right", framealpha=0.95)
    ax.grid(True, linestyle="--", alpha=0.5, axis="y")

    fig.savefig(output_path, format="svg", bbox_inches="tight")
    plt.close(fig)


def get_scenario_touch_file(scenario_name: str, config_name: str) -> Path | None:
    """Return repository file to touch for an incremental rebuild scenario."""
    if scenario_name == "touch-core-header":
        return ROOT_DIR / "vulkan" / "vulkan_hpp_macros.hpp"
    if scenario_name == "touch-root-interface":
        return ROOT_DIR / "vulkan" / ("vulkan.cppm" if config_name == "modules" else "vulkan.hpp")
    if scenario_name == "touch-intermediate-interface":
        return ROOT_DIR / "samples" / "utils" / ("utils.cppm" if config_name == "modules" else "utils.hpp")
    if scenario_name == "touch-cpp":
        return ROOT_DIR / "RAII_Samples" / "RayTracing" / "RayTracing.cpp"
    return None


def make_comparison_row(
    label: str,
    mod_st: ScenarioStats,
    pch_st: ScenarioStats,
    headers_st: ScenarioStats,
    use_wall: bool = False,
) -> list[str]:
    """Construct comparison row for markdown table."""
    mod_mean = mod_st.wall_mean if use_wall else mod_st.compiler_mean
    mod_std = mod_st.wall_stddev if use_wall else mod_st.compiler_stddev
    pch_mean = pch_st.wall_mean if use_wall else pch_st.compiler_mean
    pch_std = pch_st.wall_stddev if use_wall else pch_st.compiler_stddev
    hdr_mean = headers_st.wall_mean if use_wall else headers_st.compiler_mean
    hdr_std = headers_st.wall_stddev if use_wall else headers_st.compiler_stddev

    rel_pch = pch_mean / mod_mean if mod_mean > TimeDelta.ZERO else 0.0
    rel_headers = hdr_mean / mod_mean if mod_mean > TimeDelta.ZERO else 0.0

    return [
        label,
        f"{mod_mean.total('seconds'):.3f} (±{mod_std.total('seconds'):.2f})",
        f"{pch_mean.total('seconds'):.3f} (±{pch_std.total('seconds'):.2f})",
        f"{hdr_mean.total('seconds'):.3f} (±{hdr_std.total('seconds'):.2f})",
        f"{rel_pch:.2f}×",
        f"{rel_headers:.2f}×",
    ]


class BenchmarkSuite:
    """Orchestrate configuration, builds, sampling, and report generation."""

    def __init__(
        self,
        cmake_binary_path: str = DEFAULT_CMAKE,
        clang_binary_path: str = DEFAULT_CLANG,
        out_base_dir: Path = ROOT_DIR / "out" / "build",
        report_dir: Path = ROOT_DIR / "out" / "benchmark",
        iteration_count: int = 10,
        verbose_logging: bool = True,
    ) -> None:
        self.cmake_binary_path: str = cmake_binary_path
        self.clang_binary_path: str = clang_binary_path
        self.out_base_dir: Path = out_base_dir
        self.report_dir: Path = report_dir
        self.iteration_count: int = iteration_count
        self.verbose_logging: bool = verbose_logging
        self.results: list[BuildRunResult] = []

        self.configs: dict[str, BuildConfig] = {
            "modules": BuildConfig(
                name="modules",
                display_name="C++20 modules",
                out_dir_name="modules",
                cmake_flags=[
                    "--preset=samples",
                    "-DVULKAN_HPP_BUILD_CXX_MODULE=ON",
                    "-DVULKAN_HPP_PRECOMPILE=OFF",
                    "-DCMAKE_EXPERIMENTAL_CXX_IMPORT_STD=25d6f6aa-be65-4692-b44e-87b23e96d4e1",
                    f"-DCMAKE_CXX_COMPILER={self.clang_binary_path}",
                    "-DCMAKE_CXX_STANDARD=20",
                    "-DCMAKE_CXX_FLAGS=-ftime-trace",
                    "-G", "Ninja",
                ],
            ),
            "pch": BuildConfig(
                name="pch",
                display_name="Precompiled headers",
                out_dir_name="pch",
                cmake_flags=[
                    "--preset=samples",
                    "-DVULKAN_HPP_BUILD_CXX_MODULE=OFF",
                    "-DVULKAN_HPP_PRECOMPILE=ON",
                    "-DCMAKE_CXX_SCAN_FOR_MODULES=OFF",
                    f"-DCMAKE_CXX_COMPILER={self.clang_binary_path}",
                    "-DCMAKE_CXX_STANDARD=20",
                    "-DCMAKE_CXX_FLAGS=-ftime-trace",
                    "-G", "Ninja",
                ],
            ),
            "headers": BuildConfig(
                name="headers",
                display_name="Headers only",
                out_dir_name="headers",
                cmake_flags=[
                    "--preset=samples",
                    "-DVULKAN_HPP_BUILD_CXX_MODULE=OFF",
                    "-DVULKAN_HPP_PRECOMPILE=OFF",
                    "-DCMAKE_CXX_SCAN_FOR_MODULES=OFF",
                    f"-DCMAKE_CXX_COMPILER={self.clang_binary_path}",
                    "-DCMAKE_CXX_STANDARD=20",
                    "-DCMAKE_CXX_FLAGS=-ftime-trace",
                    "-G", "Ninja",
                ],
            ),
        }

    def log(self, message: str) -> None:
        """Print message if verbose logging is enabled."""
        if self.verbose_logging:
            print(message, flush=True)

    def configure_build(self, config: BuildConfig, fresh_configure: bool = True) -> bool:
        """Configure build tree using CMake with instrumentation enabled."""
        build_dir = self.out_base_dir / config.out_dir_name
        cmd = [self.cmake_binary_path, "-B", str(build_dir)] + config.cmake_flags
        if fresh_configure:
            cmd.append("--fresh")

        self.log(f"[{config.name}] Configuring CMake in {build_dir}...")
        res = subprocess.run(
            cmd,
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if res.returncode != 0:
            print(f"Error configuring {config.name}:\n{res.stderr}", file=sys.stderr)
            return False
        return True

    def run_build(
        self,
        config: BuildConfig,
        scenario_name: str,
        current_iteration: int,
        jobs: int | None = None,
        target: str | None = None,
    ) -> BuildRunResult:
        """Execute build using cmake --build and capture metrics."""
        build_dir = self.out_base_dir / config.out_dir_name
        ninja_log = build_dir / ".ninja_log"

        prev_lines = 0
        if ninja_log.is_file():
            with open(ninja_log, "r", encoding="utf-8", errors="replace") as f:
                prev_lines = sum(1 for _ in f)

        trace_dir = build_dir / ".cmake" / "instrumentation" / "v1" / "data" / "trace"
        if trace_dir.is_dir():
            for old_trace in trace_dir.glob("trace-*.json"):
                try:
                    old_trace.unlink()
                except OSError:
                    pass

        t_start_ns = time.perf_counter_ns()
        proc_start = os.times()

        cmd = [self.cmake_binary_path, "--build", str(build_dir)]
        if target is not None:
            cmd.extend(["--target", target])
        if jobs is not None:
            cmd.extend(["-j", str(jobs)])

        res = subprocess.run(
            cmd,
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        proc_end = os.times()
        t_end_ns = time.perf_counter_ns()

        wall_time = to_delta(t_end_ns - t_start_ns)
        user_time = to_delta(s=proc_end[2] - proc_start[2])
        sys_time = to_delta(s=proc_end[3] - proc_start[3])

        if res.returncode != 0:
            print(f"Build failed for {config.name} ({scenario_name}, run {current_iteration}):\n{res.stderr}", file=sys.stderr)

        all_targets = parse_ninja_build_log(ninja_log)
        recent_targets = all_targets[max(0, prev_lines - 1):] if prev_lines > 0 and len(all_targets) >= prev_lines - 1 else all_targets

        compiler_time, frontend_time, backend_time, compile_count, time_traces = parse_cmake_instrumentation_trace(build_dir)

        return BuildRunResult(
            config_name=config.name,
            scenario_name=scenario_name,
            run_index=current_iteration,
            compiler_time=compiler_time,
            frontend_time=frontend_time,
            backend_time=backend_time,
            wall_time=wall_time,
            user_time=user_time,
            sys_time=sys_time,
            exit_code=res.returncode,
            targets_built=compile_count if compile_count > 0 else len(recent_targets),
            ninja_targets=recent_targets,
            time_trace_files=time_traces,
            jobs=jobs if jobs is not None else 0,
        )

    def _log_run_result(self, res: BuildRunResult) -> None:
        """Print run results."""
        self.log(
            f"  -> Compiler CPU: {res.compiler_time.total('seconds'):.3f}s "
            f"(Frontend: {res.frontend_time.total('seconds'):.3f}s, Backend: {res.backend_time.total('seconds'):.3f}s) | "
            f"Wall: {res.wall_time.total('seconds'):.3f}s | Built: {res.targets_built} targets"
        )

    def clean_build_dir(self, build_dir: Path) -> None:
        """Clean build targets and ninja log."""
        subprocess.run(
            [self.cmake_binary_path, "--build", str(build_dir), "--target", "clean"],
            cwd=ROOT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        ninja_log = build_dir / ".ninja_log"
        if ninja_log.exists():
            ninja_log.unlink()

    def run_benchmark_scenario(
        self,
        config: BuildConfig,
        scenario_name: str,
        display_label: str,
        setup_fn: Callable[[], None] | None = None,
        baseline_first: bool = False,
        jobs: int | None = None,
        target: str | None = None,
        iterations: int | None = None,
    ) -> list[BuildRunResult]:
        """Execute benchmark iterations for a scenario after optional setup."""
        run_count = iterations if iterations is not None else self.iteration_count
        self.log(f"\nScenario: {display_label} [{config.display_name}] ({run_count} runs)")
        build_dir = self.out_base_dir / config.out_dir_name
        results: list[BuildRunResult] = []

        if baseline_first:
            subprocess.run(
                [self.cmake_binary_path, "--build", str(build_dir)],
                cwd=ROOT_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        for r in range(1, run_count + 1):
            if setup_fn is not None:
                setup_fn()
            self.log(f"[{config.name} : {scenario_name}] Run {r}/{run_count}...")
            res = self.run_build(config, scenario_name, r, jobs=jobs, target=target)
            results.append(res)
            self._log_run_result(res)

        return results

    def calculate_stats(self, results_list: list[BuildRunResult] | None = None) -> dict[tuple[str, str], ScenarioStats]:
        """Calculate statistics across iterations for each scenario."""
        target_results = results_list if results_list is not None else self.results
        grouped: dict[tuple[str, str], list[BuildRunResult]] = {}
        for r in target_results:
            grouped.setdefault((r.config_name, r.scenario_name), []).append(r)

        stats: dict[tuple[str, str], ScenarioStats] = {}
        for (cfg_name, scen_name), runs in grouped.items():
            n = len(runs)
            comp_times = [r.compiler_time for r in runs]
            fe_times = [r.frontend_time for r in runs]
            be_times = [r.backend_time for r in runs]
            wall_times = [r.wall_time for r in runs]
            user_times = [r.user_time for r in runs]
            sys_times = [r.sys_time for r in runs]
            targets = [r.targets_built for r in runs]

            comp_mean, comp_std, comp_med = compute_delta_stats(comp_times)
            fe_mean, fe_std, _ = compute_delta_stats(fe_times)
            be_mean, be_std, _ = compute_delta_stats(be_times)
            wall_mean, wall_std, wall_med = compute_delta_stats(wall_times)
            user_mean, _, _ = compute_delta_stats(user_times)
            sys_mean, _, _ = compute_delta_stats(sys_times)

            stats[(cfg_name, scen_name)] = ScenarioStats(
                config_name=cfg_name,
                scenario_name=scen_name,
                runs=n,
                compiler_mean=comp_mean,
                compiler_stddev=comp_std,
                compiler_min=min(comp_times) if comp_times else TimeDelta.ZERO,
                compiler_max=max(comp_times) if comp_times else TimeDelta.ZERO,
                compiler_median=comp_med,
                frontend_mean=fe_mean,
                frontend_stddev=fe_std,
                backend_mean=be_mean,
                backend_stddev=be_std,
                wall_mean=wall_mean,
                wall_stddev=wall_std,
                wall_min=min(wall_times) if wall_times else TimeDelta.ZERO,
                wall_max=max(wall_times) if wall_times else TimeDelta.ZERO,
                wall_median=wall_med,
                user_mean=user_mean,
                sys_mean=sys_mean,
                targets_mean=statistics.mean(targets) if targets else 0.0,
                raw_compiler_times=comp_times,
                raw_wall_times=wall_times,
                raw_frontend_times=fe_times,
                raw_backend_times=be_times,
                jobs=runs[0].jobs if runs else 0,
            )
        return stats

    def export_data_json(self, json_path: Path) -> None:
        """Export all benchmark run results to a JSON file."""
        def serialize_run(r: BuildRunResult) -> dict[str, Any]:
            return {
                "config_name": r.config_name,
                "scenario_name": r.scenario_name,
                "run_index": r.run_index,
                "compiler_time_seconds": r.compiler_time.total("seconds"),
                "frontend_time_seconds": r.frontend_time.total("seconds"),
                "backend_time_seconds": r.backend_time.total("seconds"),
                "wall_time_seconds": r.wall_time.total("seconds"),
                "targets_built": r.targets_built,
            }

        data = {
            "scenario_results": [serialize_run(r) for r in self.results if not r.scenario_name.startswith("scale-j")],
        }
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def import_data_json(self, json_path: Path) -> list[BuildRunResult]:
        """Import benchmark run results from a JSON file."""
        if not json_path.is_file():
            raise FileNotFoundError(f"JSON data file not found: {json_path}")
        data = json.loads(json_path.read_text(encoding="utf-8"))

        def deserialize_run(d: dict[str, Any]) -> BuildRunResult:
            return BuildRunResult(
                config_name=d["config_name"],
                scenario_name=d["scenario_name"],
                run_index=d["run_index"],
                compiler_time=to_delta(s=float(d["compiler_time_seconds"])),
                frontend_time=to_delta(s=float(d["frontend_time_seconds"])),
                backend_time=to_delta(s=float(d["backend_time_seconds"])),
                wall_time=to_delta(s=float(d["wall_time_seconds"])),
                user_time=TimeDelta.ZERO,
                sys_time=TimeDelta.ZERO,
                exit_code=0,
                targets_built=int(d["targets_built"]),
            )

        self.results = [
            deserialize_run(r)
            for r in data.get("scenario_results", [])
            if not r.get("scenario_name", "").startswith("scale-j")
        ]
        return self.results

    def import_log(self, log_path: Path) -> list[BuildRunResult]:
        """Parse build runs from an existing benchmark execution log."""
        if not log_path.is_file():
            raise FileNotFoundError(f"Log file not found: {log_path}")
        text = log_path.read_text(encoding="utf-8", errors="replace")
        pattern = re.compile(
            r"\[(?P<cfg>modules|pch|headers)\s*:\s*(?P<scen>[^\]]+)\]\s+Run\s+(?P<run>\d+)/(?P<total>\d+)\.\.\.\s*\n"
            r"\s*->\s+Compiler CPU:\s+(?P<comp>[\d\.]+)s\s+\(Frontend:\s+(?P<fe>[\d\.]+)s,\s+Backend:\s+(?P<be>[\d\.]+)s\)\s+\|\s+Wall:\s+(?P<wall>[\d\.]+)s\s+\|\s+Built:\s+(?P<targets>\d+)\s+targets"
        )
        scenario_results: list[BuildRunResult] = []
        for m in pattern.finditer(text):
            d = m.groupdict()
            scen = d["scen"]
            if scen.startswith("scale-j"):
                continue
            cfg = d["cfg"]
            comp = to_delta(s=float(d["comp"]))
            fe = to_delta(s=float(d["fe"]))
            be = to_delta(s=float(d["be"]))
            wall = to_delta(s=float(d["wall"]))
            run_idx = int(d["run"])
            targets = int(d["targets"])
            scenario_results.append(
                BuildRunResult(
                    config_name=cfg,
                    scenario_name=scen,
                    run_index=run_idx,
                    compiler_time=comp,
                    frontend_time=fe,
                    backend_time=be,
                    wall_time=wall,
                    user_time=TimeDelta.ZERO,
                    sys_time=TimeDelta.ZERO,
                    exit_code=0,
                    targets_built=targets,
                )
            )
        self.results = scenario_results
        return scenario_results

    def collect_trace_data(self, traces_destination_dir: Path) -> None:
        """Copy compilation profiles, CMake instrumentation traces, and Ninjatracing files."""
        traces_destination_dir.mkdir(parents=True, exist_ok=True)

        key_patterns: list[tuple[str, str, str]] = [
            ("modules", "vulkan.cppm.json", "modules_vulkan.cppm.json"),
            ("modules", "utils.cppm.json", "modules_utils.cppm.json"),
            ("modules", "RayTracing.cpp.json", "modules_RayTracing.cpp.json"),
            ("pch", "RayTracing.cpp.json", "pch_RayTracing.cpp.json"),
            ("headers", "RayTracing.cpp.json", "headers_RayTracing.cpp.json"),
        ]

        for cfg_key, file_pattern, dest_name in key_patterns:
            build_dir = self.out_base_dir / self.configs[cfg_key].out_dir_name
            for found in build_dir.rglob(f"*{file_pattern}"):
                shutil.copy2(found, traces_destination_dir / dest_name)
                break

        for cfg_key in self.configs:
            trace_dir = self.out_base_dir / self.configs[cfg_key].out_dir_name / ".cmake" / "instrumentation" / "v1" / "data" / "trace"
            if not trace_dir.is_dir():
                continue
            for trace_f in sorted(trace_dir.glob("trace-*.json")):
                try:
                    shutil.copy2(trace_f, traces_destination_dir / f"cmake_trace_{cfg_key}_{trace_f.name}")
                except OSError:
                    pass

        for cfg_key in self.configs:
            build_dir = self.out_base_dir / self.configs[cfg_key].out_dir_name
            ninja_log = build_dir / ".ninja_log"
            if not ninja_log.is_file():
                continue
            try:
                with open(ninja_log, "r", encoding="utf-8", errors="replace") as f:
                    options: dict[str, Any] = {"showall": False, "granularity": 50000, "embed_time_trace": True}
                    augmented = list(ninjatracing.log_to_dicts(f, 0, options))
                out_ninja_trace = traces_destination_dir / f"ninjatrace_{cfg_key}_augmented.json"
                with open(out_ninja_trace, "w", encoding="utf-8") as out_f:
                    json.dump(augmented, out_f)
                self.log(f"Generated augmented thread timeline: {out_ninja_trace.name} ({len(augmented)} slices)")
            except Exception as e:
                self.log(f"Warning: Ninjatracing generation failed for {cfg_key}: {e}")

    def generate_svg_visualizations(
        self,
        stats: dict[tuple[str, str], ScenarioStats],
        plots_dir: Path,
        scenarios: list[tuple[str, str]],
    ) -> dict[str, Path]:
        """Generate SVG box-and-whisker plots and phase breakdown chart."""
        plots_dir.mkdir(parents=True, exist_ok=True)
        generated_plots: dict[str, Path] = {}

        cfg_keys = ["modules", "pch", "headers"]
        cfg_labels = ["C++20 modules", "Precompiled headers", "Headers only"]
        scen_labels = [s[1] for s in scenarios]

        # 1. Scenarios box-and-whisker: compiler CPU time
        comp_data = [
            (cfg, label, [[d.total("seconds") for d in stats.get((cfg, s[0]), ScenarioStats.empty()).raw_compiler_times] or [0.0] for s in scenarios])
            for cfg, label in zip(cfg_keys, cfg_labels)
        ]
        p1 = plots_dir / "scenarios_compiler_time_box_plot.svg"
        render_grouped_boxplot(
            comp_data,
            scen_labels,
            "",
            "Compiler CPU time (s)",
            "Compiler CPU time across scenarios",
            p1,
            rotation=25.0,
        )
        generated_plots["scenarios_compiler"] = p1

        # 2. Scenarios box-and-whisker: wall-clock time
        wall_data = [
            (cfg, label, [[d.total("seconds") for d in stats.get((cfg, s[0]), ScenarioStats.empty()).raw_wall_times] or [0.0] for s in scenarios])
            for cfg, label in zip(cfg_keys, cfg_labels)
        ]
        p2 = plots_dir / "scenarios_wall_time_box_plot.svg"
        render_grouped_boxplot(
            wall_data,
            scen_labels,
            "",
            "Wall-clock time (s)",
            "Wall-clock build time across scenarios",
            p2,
            rotation=25.0,
        )
        generated_plots["scenarios_wall"] = p2

        # 3. Stacked bar chart: compiler phase breakdown (clean build)
        clean_stats = {cfg: stats.get((cfg, "clean")) for cfg in cfg_keys}
        fe_times = [st.frontend_mean.total("seconds") if st else 0.0 for st in clean_stats.values()]
        be_times = [st.backend_mean.total("seconds") if st else 0.0 for st in clean_stats.values()]

        x_bar = np.arange(len(cfg_labels))
        fig, ax = plt.subplots(figsize=(9.5, 5.5))
        ax.bar(x_bar, fe_times, width=0.45, label="Frontend", color="#3b82f6", edgecolor="#1d4ed8", linewidth=1.2)
        ax.bar(x_bar, be_times, width=0.45, bottom=fe_times, label="Backend", color="#10b981", edgecolor="#047857", linewidth=1.2)

        for i, (fe, be) in enumerate(zip(fe_times, be_times)):
            tot = fe + be
            if tot > 0:
                ax.text(i, tot + (max(fe_times + be_times) * 0.02), f"{tot:.2f}s", ha="center", va="bottom", fontweight="bold", fontsize=10.5)

        ax.set_xticks(x_bar)
        ax.set_xticklabels(cfg_labels, fontsize=11, fontweight="bold")
        ax.set_ylabel("Compiler CPU time (s)", fontsize=11, fontweight="bold")
        ax.set_title("Compiler phase breakdown: frontend parse vs backend codegen", fontsize=13, fontweight="bold")
        ax.legend(loc="upper right", framealpha=0.95)
        ax.grid(True, linestyle="--", alpha=0.5, axis="y")

        p3 = plots_dir / "compiler_phase_breakdown_bar_plot.svg"
        fig.savefig(p3, format="svg", bbox_inches="tight")
        plt.close(fig)
        generated_plots["phase_breakdown"] = p3

        return generated_plots

    def generate_markdown_report(
        self,
        stats: dict[tuple[str, str], ScenarioStats],
        output_report_file: Path,
        scenarios: list[tuple[str, str]],
    ) -> None:
        """Generate Markdown benchmark report using tabulate."""
        compiler_info = get_compiler_identifier(self.clang_binary_path)

        headers_exec = [
            "Scenario",
            "Modules (s)",
            "Precompiled headers (s)",
            "Headers only (s)",
            "Speedup vs pch",
            "Speedup vs headers",
        ]
        rows_exec = [
            make_comparison_row(
                scen_title,
                stats[("modules", scen_id)],
                stats[("pch", scen_id)],
                stats[("headers", scen_id)],
                use_wall=False,
            )
            for scen_id, scen_title in scenarios
            if ("modules", scen_id) in stats and ("pch", scen_id) in stats and ("headers", scen_id) in stats
        ]

        headers_wall = [
            "Scenario",
            "Modules (wall) (s)",
            "Precompiled headers (wall) (s)",
            "Headers only (wall) (s)",
            "Speedup vs pch",
            "Speedup vs headers",
        ]
        rows_wall = [
            make_comparison_row(
                scen_title,
                stats[("modules", scen_id)],
                stats[("pch", scen_id)],
                stats[("headers", scen_id)],
                use_wall=True,
            )
            for scen_id, scen_title in scenarios
            if ("modules", scen_id) in stats and ("pch", scen_id) in stats and ("headers", scen_id) in stats
        ]

        md: list[str] = [
            "# Benchmark report: C++20 modules vs headers and PCH",
            "",
            f"- Compiler: `{compiler_info}`",
            f"- `cmake`: `{self.cmake_binary_path}` (v1.1 instrumentation)",
            "- Compiler options: `-std=c++20 -ftime-trace`",
            f"- Sampling: {self.iteration_count} runs per scenario",
            "",
            f"Methodology: Statistical sampling over {self.iteration_count} runs per scenario. "
            "Compiler phase timings extracted via `cmake` instrumentation API (`trace/trace-*.json` and `clang` `-ftime-trace`). "
            "Thread timelines generated via `ninjatracing`. "
            "Precompiled headers disabled for modules (`VULKAN_HPP_PRECOMPILE=OFF`), "
            "and module scanning disabled for headers (`CMAKE_CXX_SCAN_FOR_MODULES=OFF`).",
            "",
            "## Compiler CPU time",
            "",
            "Direct compiler CPU time represents time spent inside `clang` compiler frontend and backend, "
            "isolating compilation speed from disk caching and process fork overhead.",
            "",
            tabulate(rows_exec, headers=headers_exec, tablefmt="github"),
            "",
            "![Compiler CPU time across scenarios](plots/scenarios_compiler_time_box_plot.svg)",
            "",
            "## Wall-clock build time",
            "",
            "Wall-clock build duration across end-to-end execution of `cmake --build`.",
            "",
            tabulate(rows_wall, headers=headers_wall, tablefmt="github"),
            "",
            "![Wall-clock build time across scenarios](plots/scenarios_wall_time_box_plot.svg)",
            "",
        ]

        md.extend([
            "## Compiler phase breakdown",
            "",
            "`clang` `-ftime-trace` separates frontend parse (source lexing, macro expansion, header parsing, module BMI loading, template instantiation) "
            "and backend codegen (LLVM optimization passes, instruction selection, code generation).",
            "",
        ])
        headers_phase = [
            "Configuration",
            "Compiler CPU (s)",
            "Frontend (s)",
            "Backend (s)",
            "Wall time (s)",
            "Targets",
        ]
        for scen_id, scen_title in scenarios:
            md.append(f"### {scen_title}")
            md.append("")
            rows_phase = []
            for cfg_id in ["modules", "pch", "headers"]:
                st = stats.get((cfg_id, scen_id))
                if not st:
                    continue
                rows_phase.append([
                    self.configs[cfg_id].display_name,
                    f"{st.compiler_mean.total('seconds'):.3f} (±{st.compiler_stddev.total('seconds'):.2f})",
                    f"{st.frontend_mean.total('seconds'):.3f}",
                    f"{st.backend_mean.total('seconds'):.3f}",
                    f"{st.wall_mean.total('seconds'):.3f}",
                    f"{st.targets_mean:.1f}",
                ])
            md.append(tabulate(rows_phase, headers=headers_phase, tablefmt="github"))
            md.append("")

        md.extend([
            "![Compiler phase breakdown: frontend parse vs backend codegen](plots/compiler_phase_breakdown_bar_plot.svg)",
            "",
            "## Trace inspection guide",
            "",
            "Chromium Trace Event profiles captured using `cmake` instrumentation and augmented `.ninja_log` timelines via `ninjatracing`:",
            "",
            "### 1. `speedscope` (flame graphs)",
            "",
            "Inspect flame graphs directly via `npx`:",
            "```bash",
            "npx speedscope out/benchmark/traces/modules_RayTracing.cpp.json",
            "npx speedscope out/benchmark/traces/ninjatrace_modules_augmented.json",
            "```",
            "",
            "### 2. `perfetto` (waterfall timelines)",
            "",
            "1. Open [ui.perfetto.dev](https://ui.perfetto.dev/) in your browser.",
            "2. Click **Open trace file** and select any `.json` from `out/benchmark/traces/`.",
            "3. Inspect parallel schedule across worker threads and drill down into individual compiler slices.",
            "",
        ])

        output_report_file.parent.mkdir(parents=True, exist_ok=True)
        output_report_file.write_text("\n".join(md), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Vulkan-Hpp C++20 modules vs header-only benchmark suite")
    parser.add_argument("--runs", type=int, default=10, help="Number of iterations per scenario (default: 10)")
    parser.add_argument("-j", "--jobs", type=int, default=None, help="Parallel build jobs for ninja (default: ninja default)")
    parser.add_argument("--cmake", type=str, default=DEFAULT_CMAKE, help="Path to cmake executable")
    parser.add_argument("--clang", type=str, default=DEFAULT_CLANG, help="Path to clang++ executable")
    parser.add_argument("--build-base", type=str, default=str(ROOT_DIR / "out" / "build"), help="Base directory for build configs")
    parser.add_argument("--report-dir", type=str, default=str(ROOT_DIR / "out" / "benchmark"), help="Directory for reports and traces")
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=["clean", "touch-core-header", "touch-root-interface", "touch-intermediate-interface", "touch-cpp"],
        help="Scenarios to run",
    )
    parser.add_argument("--configs", nargs="+", default=["modules", "pch", "headers"], help="Configurations to run")
    parser.add_argument("--import-log", type=str, default=None, help="Import run data from existing log file and skip builds")
    parser.add_argument("--import-data", type=str, default=None, help="Import run data from JSON file and skip builds")
    parser.add_argument("--export-data", type=str, default=None, help="Export run data to JSON file")
    args = parser.parse_args()

    build_base = Path(args.build_base).resolve()
    report_dir = Path(args.report_dir).resolve()
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

    print("Vulkan-Hpp C++20 modules vs headers and PCH benchmark suite (CMake instrumentation)")
    print(f"Compiler: {compiler_info} ({args.clang})")
    print(f"CMake: {args.cmake}")
    print(f"Iterations: {args.runs}")
    print(f"Configurations: {', '.join(args.configs)}")
    print(f"Scenarios: {', '.join(args.scenarios)}")
    print(f"Build base: {build_base}")
    print(f"Report directory: {report_dir}\n")

    scenario_definitions: list[tuple[str, str]] = [
        ("clean", "Clean build"),
        ("touch-core-header", "Touch `vulkan_hpp_macros.hpp`"),
        ("touch-root-interface", "Touch `vulkan.cppm` / `vulkan.hpp`"),
        ("touch-intermediate-interface", "Touch `utils.cppm` / `utils.hpp`"),
        ("touch-cpp", "Touch `RayTracing.cpp`"),
    ]
    active_scenarios = [s for s in scenario_definitions if s[0] in args.scenarios]

    if args.import_log:
        print(f"Importing run data from log: {args.import_log}")
        suite.import_log(Path(args.import_log))
    elif args.import_data:
        print(f"Importing run data from JSON: {args.import_data}")
        suite.import_data_json(Path(args.import_data))
    else:
        # 1. Configure builds with CMake instrumentation
        for cfg_key in args.configs:
            cfg = suite.configs[cfg_key]
            if not suite.configure_build(cfg, fresh_configure=True):
                sys.exit(1)

        # 2. Run scenario benchmarks
        for cfg_key in args.configs:
            cfg = suite.configs[cfg_key]
            build_dir = suite.out_base_dir / cfg.out_dir_name

            for scen_id, scen_title in active_scenarios:
                if scen_id == "clean":
                    runs = suite.run_benchmark_scenario(
                        cfg,
                        scen_id,
                        scen_title,
                        setup_fn=lambda b=build_dir: suite.clean_build_dir(b),
                        jobs=args.jobs,
                    )
                else:
                    touch_f = get_scenario_touch_file(scen_id, cfg.name)
                    runs = suite.run_benchmark_scenario(
                        cfg,
                        scen_id,
                        scen_title,
                        setup_fn=touch_f.touch if touch_f else None,
                        baseline_first=True,
                        jobs=args.jobs,
                    )
                suite.results.extend(runs)

    # Export dataset to JSON
    data_json_path = Path(args.export_data) if args.export_data else report_dir / "benchmark_data.json"
    suite.export_data_json(data_json_path)
    print(f"Exported benchmark data to: {data_json_path}")

    # 3. Calculate statistics and generate visualizations
    stats = suite.calculate_stats()
    suite.collect_trace_data(traces_dir)
    print(f"\nCollected trace data and Ninjatracing profiles in: {traces_dir}")

    plots = suite.generate_svg_visualizations(
        stats,
        plots_dir,
        active_scenarios,
    )
    print(f"Generated SVG box-and-whisker plots in: {plots_dir} ({len(plots)} plots)")

    # 4. Generate Markdown report
    report_path = report_dir / "BENCHMARK_REPORT.md"
    suite.generate_markdown_report(
        stats,
        report_path,
        active_scenarios,
    )
    print(f"Benchmark report generated: {report_path}")

    print("\nBenchmark results summary (compiler CPU duration):")
    for (cfg_name, scen_name), st in sorted(stats.items()):
        print(
            f"  [{cfg_name:8s}] {scen_name:28s} : {st.compiler_mean.total('seconds'):7.3f}s "
            f"(Frontend: {st.frontend_mean.total('seconds'):6.3f}s, Backend: {st.backend_mean.total('seconds'):6.3f}s) "
            f"[Wall: {st.wall_mean.total('seconds'):6.3f}s]"
        )


if __name__ == "__main__":
    main()
