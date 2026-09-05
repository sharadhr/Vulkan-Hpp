from collections import Counter
from collections.abc import Sequence, Set as AbstractSet
import json
from pathlib import Path
from typing import Any
from whenever import TimeDelta

from .models import TargetExecution, TargetTypeBreakdown, to_delta


def classify_target_output(output_path: str) -> str:
    """categorize a target output path into compilation, scan, dyndep, link, or custom."""
    target_path = Path(output_path)
    # CMake exposes only output paths in .ninja_log, so phase attribution relies on its stable suffixes.
    match target_path.name:
        case "CXXModules.json":
            return "dyndep"
        case _:
            match target_path.suffix.lower():
                case ".o" | ".obj" | ".pcm" | ".bmi" | ".pch":
                    return "compilation"
                case ".ddi":
                    return "scan"
                case ".dd" | ".modmap":
                    return "dyndep"
                case ".a" | ".dll" | ".dylib" | ".exe" | ".lib" | ".so":
                    return "link"
                case _ if target_path.parent == Path(".") or not target_path.suffix:
                    return "link"
                case _:
                    return "custom"


def extract_target_type_breakdown(targets: Sequence[TargetExecution]) -> TargetTypeBreakdown:
    """aggregate target executions into counts by phase category."""
    # Keep every executed Ninja edge: modules add scan and dyndep work that compile-only totals would hide.
    counts = Counter(classify_target_output(target.output) for target in targets)

    return TargetTypeBreakdown(
        compilations=counts["compilation"],
        scans=counts["scan"],
        dynamic_dependencies=counts["dyndep"],
        links=counts["link"],
        custom_commands=counts["custom"],
    )


def snapshot_ninja_build_log(log_path: Path) -> set[str]:
    """capture the current .ninja_log records so a later parse can isolate the next run's targets."""
    if not log_path.is_file():
        return set()

    # Preserve full records, including timestamps and command hashes, to distinguish a rebuilt output.
    with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
        return {stripped for raw_line in log_file if (stripped := raw_line.strip()) and not stripped.startswith("#")}


def parse_ninja_build_log(
    log_path: Path,
    previous_entries: AbstractSet[str] | None = None,
) -> list[TargetExecution]:
    """extract targets built during the current run from .ninja_log.

    Ninja appends target completion records on incremental builds, but it also periodically
    recompacts the log, rewriting it as one deduplicated record per output. positional offsets
    are therefore unstable across runs, so records are isolated by diffing against a snapshot
    taken before the build; recompaction preserves surviving records verbatim.
    """
    if not log_path.is_file():
        return []

    # A record diff survives Ninja's periodic log compaction, unlike a positional line offset.
    seen_entries = previous_entries if previous_entries is not None else frozenset()
    target_executions: list[TargetExecution] = []
    with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
        for raw_line in log_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or line in seen_entries:
                continue
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            try:
                start_time = to_delta(ms=int(parts[0]))
                end_time = to_delta(ms=int(parts[1]))
                target_executions.append(
                    TargetExecution(
                        start=start_time,
                        end=end_time,
                        duration=max(TimeDelta.ZERO, end_time - start_time),
                        output=parts[3],
                        command_hash=parts[4],
                    )
                )
            except ValueError:
                continue
    return target_executions


def parse_clang_ftime_trace(trace_path: Path) -> tuple[TimeDelta, TimeDelta, TimeDelta]:
    """extract compiler total, frontend, and backend durations from a Clang -ftime-trace profile.

    Clang writes Chrome Trace Event format JSON objects where phase "X" denotes duration in microseconds:
    - ExecuteCompiler: total translation unit compile duration.
    - Frontend: source parsing, macro expansion, header inclusion, C++20 module interface loading,
      and template instantiation.
    - Backend: LLVM intermediate representation optimization passes and machine code emission.
    """
    zero_delta = TimeDelta.ZERO
    if not trace_path.is_file():
        return zero_delta, zero_delta, zero_delta

    try:
        with open(trace_path, "r", encoding="utf-8") as trace_file:
            trace_data = json.load(trace_file)
    except (json.JSONDecodeError, OSError):
        return zero_delta, zero_delta, zero_delta

    if not isinstance(trace_data, dict):
        return zero_delta, zero_delta, zero_delta

    events = trace_data.get("traceEvents", [])
    compiler_microseconds = 0.0
    frontend_microseconds = 0.0
    backend_microseconds = 0.0

    # -ftime-trace can contain nested events; only complete-duration events are additive per translation unit.
    for event in events:
        if not isinstance(event, dict) or event.get("ph") != "X":
            continue
        duration = event.get("dur", 0)
        if not isinstance(duration, (int, float)):
            continue
        event_name = event.get("name")
        if event_name == "ExecuteCompiler":
            compiler_microseconds += float(duration)
        elif event_name == "Frontend":
            frontend_microseconds += float(duration)
        elif event_name == "Backend":
            backend_microseconds += float(duration)

    return (
        to_delta(us=compiler_microseconds),
        to_delta(us=frontend_microseconds),
        to_delta(us=backend_microseconds),
    )


def find_latest_cmake_trace(trace_dir: Path) -> Path | None:
    """find newest trace-*.json file in trace_dir, ignoring skeleton files smaller than 10 bytes."""
    if not trace_dir.is_dir():
        return None
    candidates = [
        candidate_path
        for candidate_path in trace_dir.glob("trace-*.json")
        if candidate_path.stat().st_size > 10
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate_path: candidate_path.stat().st_mtime_ns)


def resolve_event_trace_path(
    event_arguments: dict[str, Any],
    data_dir: Path,
    build_dir: Path,
) -> Path | None:
    """locate Clang trace matching a CMake compile event via traceFile parameter or object output name."""
    if "traceFile" in event_arguments and event_arguments["traceFile"]:
        candidate_path = data_dir / event_arguments["traceFile"]
        if candidate_path.is_file():
            return candidate_path

    outputs = event_arguments.get("outputs", [])
    if outputs:
        working_directory = Path(event_arguments.get("workingDir", build_dir))
        candidate_path = working_directory / Path(outputs[0]).with_suffix(".json")
        if candidate_path.is_file():
            return candidate_path
    return None


def parse_cmake_instrumentation_trace(
    build_dir: Path,
) -> tuple[TimeDelta, TimeDelta, TimeDelta, TimeDelta, int, list[Path]]:
    """extract build duration and per-target Clang compile times from CMake instrumentation files.

    excludes clang-scan-deps invocations and .ddi (dyndep information) files because dependency scanning
    is graph generation rather than source compilation. captures the top-level cmakeBuild event duration
    directly from CMake internal timing.
    """
    trace_directory = build_dir / ".cmake" / "instrumentation" / "v1" / "data" / "trace"
    latest_trace_file = find_latest_cmake_trace(trace_directory)
    zero_delta = TimeDelta.ZERO
    if not latest_trace_file:
        return zero_delta, zero_delta, zero_delta, zero_delta, 0, []

    try:
        with open(latest_trace_file, "r", encoding="utf-8") as trace_file:
            events = json.load(trace_file)
    except (json.JSONDecodeError, OSError):
        return zero_delta, zero_delta, zero_delta, zero_delta, 0, []

    if not isinstance(events, list):
        return zero_delta, zero_delta, zero_delta, zero_delta, 0, []

    data_directory = build_dir / ".cmake" / "instrumentation" / "v1" / "data"
    cmake_build_duration = zero_delta
    total_compiler_time = zero_delta
    total_frontend_time = zero_delta
    total_backend_time = zero_delta
    compile_step_count = 0
    collected_trace_files: list[Path] = []

    # Instrumentation includes setup and dependency discovery; count only compiler invocations as compiler time.
    for event in events:
        if not isinstance(event, dict):
            continue

        category = event.get("cat", "")
        arguments = event.get("args", {})
        if not isinstance(arguments, dict):
            arguments = {}

        # capture top-level cmakeBuild duration
        if category == "cmakeBuild" or arguments.get("role") == "cmakeBuild":
            cmake_build_duration = to_delta(us=float(event.get("dur", 0)))
            continue

        if category != "compile" and arguments.get("role") != "compile":
            continue

        outputs = arguments.get("outputs", [])
        command_string = arguments.get("command", "")
        # Scans construct the module graph but do not compile a source translation unit.
        if "clang-scan-deps" in command_string or (outputs and outputs[0].endswith(".ddi")):
            continue

        compile_step_count += 1
        resolved_trace_path = resolve_event_trace_path(arguments, data_directory, build_dir)
        if resolved_trace_path:
            collected_trace_files.append(resolved_trace_path)
            compiler_delta, frontend_delta, backend_delta = parse_clang_ftime_trace(resolved_trace_path)
            event_duration = to_delta(us=float(event.get("dur", 0)))
            total_compiler_time += compiler_delta if compiler_delta > zero_delta else event_duration
            total_frontend_time += frontend_delta
            total_backend_time += backend_delta
        else:
            total_compiler_time += to_delta(us=float(event.get("dur", 0)))

    return (
        cmake_build_duration,
        total_compiler_time,
        total_frontend_time,
        total_backend_time,
        compile_step_count,
        collected_trace_files,
    )
