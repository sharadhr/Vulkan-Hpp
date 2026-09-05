"""process execution, CMake orchestration, and benchmark execution engine."""

from collections.abc import Callable, Sequence
import json
from pathlib import Path
import shutil
import subprocess
import sys
import timeit
from typing import Any
from whenever import TimeDelta

# ensure parent scripts directory is in sys.path to import local ninjatracing module
scripts_directory = Path(__file__).resolve().parent.parent
if str(scripts_directory) not in sys.path:
    sys.path.insert(0, str(scripts_directory))
import ninjatracing

from .constants import DEFAULT_CLANG, DEFAULT_CMAKE, ROOT_DIR
from .data import export_data_json, import_data_json, import_log
from .models import (
    BuildConfig,
    BuildRunResult,
    ScenarioStats,
    TargetExecution,
    TargetTypeBreakdown,
    TimedCommandResult,
    to_delta,
)
from .parsers import (
    extract_target_type_breakdown,
    parse_cmake_instrumentation_trace,
    parse_ninja_build_log,
    snapshot_ninja_build_log,
)
from .stats import calculate_all_stats


def run_timed_subprocess(
    command_arguments: list[str],
    cwd: Path | str | None = None,
    capture_output: bool = True,
) -> TimedCommandResult:
    """execute a subprocess while timing wall duration via timeit.

    uses timeit to measure wall clock duration cleanly without manual timestamp differences.
    """
    process_result: subprocess.CompletedProcess[str] | None = None

    def execute_command() -> None:
        nonlocal process_result
        process_result = subprocess.run(
            command_arguments,
            cwd=cwd,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            text=True,
        )

    wall_seconds = timeit.timeit(execute_command, number=1)

    assert process_result is not None
    return TimedCommandResult(
        returncode=process_result.returncode,
        stdout=process_result.stdout if capture_output else "",
        stderr=process_result.stderr if capture_output else "",
        wall_time=to_delta(s=wall_seconds),
    )


def get_compiler_identifier(compiler_path: str) -> str:
    """retrieve compiler identification string by invoking --version."""
    try:
        result = subprocess.run(
            [compiler_path, "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip().split("\n")[0]
    except Exception:
        return compiler_path


def get_scenario_touch_file(scenario_name: str, config_name: str) -> Path | None:
    """map an incremental scenario to the repository file that simulates the intended edit.

    target selection rationale:
    - touch-core-header: touches vulkan_hpp_macros.hpp to trigger a full rebuild cascade across
      all translation units that include Vulkan-Hpp.
    - touch-root-interface: touches vulkan.cppm (modules) or vulkan.hpp (headers/PCH) to test
      the cost of altering the root interface boundary and rebuilding built module interfaces (BMI).
    - touch-intermediate-interface: touches utils.cppm or utils.hpp to measure rebuild propagation
      at an intermediate library boundary.
    - touch-cpp: touches RayTracing.cpp to isolate turnaround time on a leaf translation unit
      without triggering downstream rebuild cascades.
    """
    if scenario_name == "touch-core-header":
        return ROOT_DIR / "vulkan" / "vulkan_hpp_macros.hpp"
    if scenario_name == "touch-root-interface":
        return ROOT_DIR / "vulkan" / ("vulkan.cppm" if config_name == "modules" else "vulkan.hpp")
    if scenario_name == "touch-intermediate-interface":
        return ROOT_DIR / "samples" / "utils" / ("utils.cppm" if config_name == "modules" else "utils.hpp")
    if scenario_name == "touch-cpp":
        return ROOT_DIR / "RAII_Samples" / "RayTracing" / "RayTracing.cpp"
    return None


class BenchmarkSuite:
    """orchestrates CMake configuration, scenario runs, trace extraction, and report generation."""

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

        # configuration variants:
        # - modules: C++20 named modules enabled with reduced BMI emission.
        #   CMAKE_EXPERIMENTAL_CXX_IMPORT_STD: UUID gate required by CMake 3.30+ to allow
        #   standard library module imports (import std;).
        #   CMAKE_LINKER_TYPE=LLD: configures lld linker across all targets.
        # - pch: precompiled header enabled for vulkan.hpp and vulkan_raii.hpp.
        #   CMAKE_CXX_SCAN_FOR_MODULES=OFF: avoids clang-scan-deps overhead in non-module builds.
        # - headers: baseline header-only build with module scanning disabled.
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
                    "-DCMAKE_CXX_FLAGS=-ftime-trace -fmodules-reduced-bmi -Wno-reduced-bmi-output-overrided -Qunused-arguments",
                    "-DCMAKE_LINKER_TYPE=LLD",
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
                    "-DCMAKE_CXX_FLAGS=-ftime-trace -Qunused-arguments",
                    "-DCMAKE_LINKER_TYPE=LLD",
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
                    "-DCMAKE_CXX_FLAGS=-ftime-trace -Qunused-arguments",
                    "-DCMAKE_LINKER_TYPE=LLD",
                    "-G", "Ninja",
                ],
            ),
        }

    def log(self, message: str) -> None:
        """write progress message to stdout when verbose logging is enabled."""
        if self.verbose_logging:
            print(message, flush=True)

    def configure_build(self, config: BuildConfig, fresh_configure: bool = True) -> bool:
        """run CMake configuration with --fresh to reset cache and activate instrumentation hooks."""
        build_directory = self.out_base_dir / config.out_dir_name
        command_arguments = [self.cmake_binary_path, "-B", str(build_directory)] + config.cmake_flags
        if fresh_configure:
            command_arguments.append("--fresh")

        self.log(f"[{config.name}] Configuring CMake in {build_directory}...")
        result = subprocess.run(
            command_arguments,
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            print(f"Error configuring {config.name}:\n{result.stderr}", file=sys.stderr)
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
        """execute build and directly collect execution time, CPU metrics, and phase traces."""
        build_directory = self.out_base_dir / config.out_dir_name
        ninja_log_path = build_directory / ".ninja_log"

        # snapshot existing Ninja records so only newly executed targets are attributed to this run
        previous_entries = snapshot_ninja_build_log(ninja_log_path)

        # delete previous traces so only artifacts from this run are parsed
        trace_directory = build_directory / ".cmake" / "instrumentation" / "v1" / "data" / "trace"
        if trace_directory.is_dir():
            for old_trace in trace_directory.glob("trace-*.json"):
                try:
                    old_trace.unlink()
                except OSError:
                    pass

        build_arguments = [self.cmake_binary_path, "--build", str(build_directory)]
        if target is not None:
            build_arguments.extend(["--target", target])
        if jobs is not None:
            build_arguments.extend(["-j", str(jobs)])

        # execute build and capture process timings
        timed_result = run_timed_subprocess(build_arguments, cwd=ROOT_DIR)

        if timed_result.returncode != 0:
            print(
                f"Build failed for {config.name} ({scenario_name}, run {current_iteration}):\n{timed_result.stderr}",
                file=sys.stderr,
            )

        # parse targets appended during this run
        recent_targets: list[TargetExecution] = parse_ninja_build_log(
            ninja_log_path,
            previous_entries=previous_entries,
        )

        # extract compilation phases from Clang and CMake traces
        (
            cmake_duration,
            compiler_time,
            frontend_time,
            backend_time,
            compile_count,
            time_traces,
        ) = parse_cmake_instrumentation_trace(build_directory)

        # prefer direct duration from CMake instrumentation over wall time when available
        effective_wall_time = cmake_duration if cmake_duration > TimeDelta.ZERO else timed_result.wall_time

        target_breakdown = (
            extract_target_type_breakdown(recent_targets)
            if recent_targets
            else TargetTypeBreakdown(compilations=compile_count)
        )

        return BuildRunResult(
            config_name=config.name,
            scenario_name=scenario_name,
            run_index=current_iteration,
            compiler_time=compiler_time,
            frontend_time=frontend_time,
            backend_time=backend_time,
            wall_time=effective_wall_time,
            exit_code=timed_result.returncode,
            targets_built=len(recent_targets) if recent_targets else compile_count,
            ninja_targets=recent_targets,
            time_trace_files=time_traces,
            jobs=jobs if jobs is not None else 0,
            target_breakdown=target_breakdown,
        )

    def _log_run_result(self, build_run: BuildRunResult) -> None:
        """print run result summary."""
        self.log(
            f"  -> Compiler CPU: {build_run.compiler_time.total('seconds'):.3f}s "
            f"(Frontend: {build_run.frontend_time.total('seconds'):.3f}s, Backend: {build_run.backend_time.total('seconds'):.3f}s) | "
            f"Wall: {build_run.wall_time.total('seconds'):.3f}s | Built: {build_run.targets_built} targets"
        )

    def clean_build_dir(self, build_dir: Path) -> None:
        """purge built targets and delete .ninja_log so subsequent runs do not inherit state."""
        subprocess.run(
            [self.cmake_binary_path, "--build", str(build_dir), "--target", "clean"],
            cwd=ROOT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        ninja_log_path = build_dir / ".ninja_log"
        if ninja_log_path.exists():
            ninja_log_path.unlink()

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
        """execute benchmark iterations for a scenario after optional baseline preparation."""
        run_count = iterations if iterations is not None else self.iteration_count
        self.log(f"\nScenario: {display_label} [{config.display_name}] ({run_count} runs)")
        build_directory = self.out_base_dir / config.out_dir_name
        scenario_results: list[BuildRunResult] = []

        # complete an initial build so incremental runs measure only modified dependencies
        if baseline_first:
            subprocess.run(
                [self.cmake_binary_path, "--build", str(build_directory)],
                cwd=ROOT_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        for iteration in range(1, run_count + 1):
            if setup_fn is not None:
                setup_fn()
            self.log(f"[{config.name} : {scenario_name}] Run {iteration}/{run_count}...")
            build_run = self.run_build(config, scenario_name, iteration, jobs=jobs, target=target)
            scenario_results.append(build_run)
            self._log_run_result(build_run)

        return scenario_results

    def calculate_stats(
        self,
        results_list: Sequence[BuildRunResult] | None = None,
    ) -> dict[tuple[str, str], ScenarioStats]:
        """compute aggregate statistics across completed runs."""
        target_results = results_list if results_list is not None else self.results
        return calculate_all_stats(target_results)

    def export_data_json(self, json_path: Path) -> None:
        """save benchmark samples to a JSON file."""
        export_data_json(self.results, json_path)

    def import_data_json(self, json_path: Path) -> list[BuildRunResult]:
        """load benchmark samples from a JSON file."""
        self.results = import_data_json(json_path)
        return self.results

    def import_log(self, log_path: Path) -> list[BuildRunResult]:
        """extract benchmark samples from raw terminal output."""
        self.results = import_log(log_path)
        return self.results

    def collect_trace_data(self, traces_destination_dir: Path) -> None:
        """copy Clang profiles and generate Ninjatracing thread timelines in traces_destination_dir."""
        traces_destination_dir.mkdir(parents=True, exist_ok=True)

        key_patterns: list[tuple[str, str, str]] = [
            ("modules", "vulkan.cppm.json", "modules_vulkan.cppm.json"),
            ("modules", "utils.cppm.json", "modules_utils.cppm.json"),
            ("modules", "RayTracing.cpp.json", "modules_RayTracing.cpp.json"),
            ("pch", "RayTracing.cpp.json", "pch_RayTracing.cpp.json"),
            ("headers", "RayTracing.cpp.json", "headers_RayTracing.cpp.json"),
        ]

        for config_key, file_pattern, destination_filename in key_patterns:
            build_directory = self.out_base_dir / self.configs[config_key].out_dir_name
            for found_trace in build_directory.rglob(f"*{file_pattern}"):
                shutil.copy2(found_trace, traces_destination_dir / destination_filename)
                break

        for config_key in self.configs:
            trace_directory = (
                self.out_base_dir / self.configs[config_key].out_dir_name / ".cmake" / "instrumentation" / "v1" / "data" / "trace"
            )
            if not trace_directory.is_dir():
                continue
            for trace_file in sorted(trace_directory.glob("trace-*.json")):
                try:
                    shutil.copy2(trace_file, traces_destination_dir / f"cmake_trace_{config_key}_{trace_file.name}")
                except OSError:
                    pass

        for config_key in self.configs:
            build_directory = self.out_base_dir / self.configs[config_key].out_dir_name
            ninja_log_path = build_directory / ".ninja_log"
            if not ninja_log_path.is_file():
                continue
            try:
                with open(ninja_log_path, "r", encoding="utf-8", errors="replace") as log_file:
                    options: dict[str, Any] = {"showall": False, "granularity": 50000, "embed_time_trace": True}
                    augmented = list(ninjatracing.log_to_dicts(log_file, 0, options))
                out_ninja_trace = traces_destination_dir / f"ninjatrace_{config_key}_augmented.json"
                with open(out_ninja_trace, "w", encoding="utf-8") as out_file:
                    json.dump(augmented, out_file)
                self.log(f"Generated augmented thread timeline: {out_ninja_trace.name} ({len(augmented)} slices)")
            except Exception as exception:
                self.log(f"Warning: Ninjatracing generation failed for {config_key}: {exception}")
