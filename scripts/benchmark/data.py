"""serialization and deserialization utilities for benchmark run datasets and logs."""

from collections.abc import Sequence
import json
from pathlib import Path
import re
from typing import Any

from .models import BuildRunResult, TargetTypeBreakdown, to_delta


def export_data_json(results: Sequence[BuildRunResult], json_path: Path) -> None:
    """serialize scenario timing results to JSON for offline analysis and report rendering."""
    def serialize_run(build_run: BuildRunResult) -> dict[str, Any]:
        return {
            "config_name": build_run.config_name,
            "scenario_name": build_run.scenario_name,
            "run_index": build_run.run_index,
            "compiler_time_seconds": build_run.compiler_time.total("seconds"),
            "frontend_time_seconds": build_run.frontend_time.total("seconds"),
            "backend_time_seconds": build_run.backend_time.total("seconds"),
            "wall_time_seconds": build_run.wall_time.total("seconds"),
            "targets_built": build_run.targets_built,
            "compilations": build_run.target_breakdown.compilations,
            "scans": build_run.target_breakdown.scans,
            "dynamic_dependencies": build_run.target_breakdown.dynamic_dependencies,
            "links": build_run.target_breakdown.links,
            "custom_commands": build_run.target_breakdown.custom_commands,
            "total_targets": build_run.target_breakdown.total or build_run.targets_built,
        }

    dataset = {
        "scenario_results": [
            serialize_run(build_run)
            for build_run in results
            if not build_run.scenario_name.startswith("scale-j")
        ],
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(dataset, indent=2), encoding="utf-8")


def import_data_json(json_path: Path) -> list[BuildRunResult]:
    """load previously exported JSON benchmark results to generate reports without rebuilding."""
    if not json_path.is_file():
        raise FileNotFoundError(f"JSON data file not found: {json_path}")

    dataset = json.loads(json_path.read_text(encoding="utf-8"))

    def deserialize_run(record: dict[str, Any]) -> BuildRunResult:
        targets_built = int(record["targets_built"])
        compilations = int(record.get("compilations", targets_built))
        scans = int(record.get("scans", 0))
        dynamic_dependencies = int(record.get("dynamic_dependencies", 0))
        links = int(record.get("links", 0))
        custom_commands = int(record.get("custom_commands", 0))
        target_breakdown = TargetTypeBreakdown(
            compilations=compilations,
            scans=scans,
            dynamic_dependencies=dynamic_dependencies,
            links=links,
            custom_commands=custom_commands,
        )
        return BuildRunResult(
            config_name=record["config_name"],
            scenario_name=record["scenario_name"],
            run_index=record["run_index"],
            compiler_time=to_delta(s=float(record["compiler_time_seconds"])),
            frontend_time=to_delta(s=float(record["frontend_time_seconds"])),
            backend_time=to_delta(s=float(record["backend_time_seconds"])),
            wall_time=to_delta(s=float(record["wall_time_seconds"])),
            exit_code=0,
            targets_built=targets_built,
            target_breakdown=target_breakdown,
        )

    return [
        deserialize_run(record)
        for record in dataset.get("scenario_results", [])
        if not record.get("scenario_name", "").startswith("scale-j")
    ]


def import_log(log_path: Path) -> list[BuildRunResult]:
    """parse benchmark measurements from raw terminal logs using regex matching."""
    if not log_path.is_file():
        raise FileNotFoundError(f"Log file not found: {log_path}")

    log_content = log_path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        r"\[(?P<configuration>modules|pch|headers)\s*:\s*(?P<scenario>[^\]]+)\]\s+Run\s+(?P<run_index>\d+)/(?P<total_runs>\d+)\.\.\.\s*\n"
        r"\s*->\s+Compiler CPU:\s+(?P<compiler>[\d\.]+)s\s+\(Frontend:\s+(?P<frontend>[\d\.]+)s,\s+Backend:\s+(?P<backend>[\d\.]+)s\)\s+\|\s+Wall:\s+(?P<wall>[\d\.]+)s\s+\|\s+Built:\s+(?P<targets>[\d]+)\s+targets"
    )

    imported_runs: list[BuildRunResult] = []
    for match in pattern.finditer(log_content):
        match_groups = match.groupdict()
        scenario_name = match_groups["scenario"]
        if scenario_name.startswith("scale-j"):
            continue
        targets_count = int(match_groups["targets"])
        imported_runs.append(
            BuildRunResult(
                config_name=match_groups["configuration"],
                scenario_name=scenario_name,
                run_index=int(match_groups["run_index"]),
                compiler_time=to_delta(s=float(match_groups["compiler"])),
                frontend_time=to_delta(s=float(match_groups["frontend"])),
                backend_time=to_delta(s=float(match_groups["backend"])),
                wall_time=to_delta(s=float(match_groups["wall"])),
                exit_code=0,
                targets_built=targets_count,
                target_breakdown=TargetTypeBreakdown(compilations=targets_count),
            )
        )
    return imported_runs
