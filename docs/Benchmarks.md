<!--
SPDX-FileCopyrightText: 2026 The Khronos Group, Inc.
SPDX-License-Identifier: Apache-2.0
-->

# Benchmarks

This document describes how to run build benchmarks comparing C++20 named modules against precompiled headers (PCH) and header-only builds in Vulkan-Hpp.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (or standard Python 3.11+)
- CMake 3.30 or newer (required for C++20 module build and instrumentation support)
- Clang 18 or newer with `lld` linker
- Ninja build generator

The benchmark suite specifies dependencies (`whenever`, `matplotlib`, `tabulate`, `dominate`, `sciform`) in `pyproject.toml`. `uv` resolves and installs dependencies into a cached environment automatically. To install `uv`, see the [official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

### Alternative: running without uv

If you do not install `uv`, create a virtual environment and install dependencies from `pyproject.toml`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
python3 scripts/benchmark_cpp20_modules.py
```

## Running the benchmark

### Basic execution

Run all benchmark configurations (`modules`, `pch`, `headers`) across all scenarios with 10 iterations:

```bash
uv run scripts/benchmark_cpp20_modules.py
```

### Specifying iterations and parallel jobs

Set iteration count with `--runs` and build parallelism with `-j`:

```bash
uv run scripts/benchmark_cpp20_modules.py --runs 5 -j 8
```

### Selecting configurations and scenarios

Select specific build configurations (`modules`, `pch`, `headers`):

```bash
uv run scripts/benchmark_cpp20_modules.py --configs modules pch
```

Select specific rebuild scenarios:

```bash
uv run scripts/benchmark_cpp20_modules.py --scenarios clean touch-root-interface touch-cpp
```

Available scenarios:

- `clean`: full rebuild of all targets.
- `touch-root-interface`: touches `vulkan.cppm` (modules) or `vulkan.hpp` (headers/PCH) to test root interface boundary changes.
- `touch-core-header`: touches `vulkan_hpp_macros.hpp` to trigger a rebuild cascade across translation units.
- `touch-intermediate-interface`: touches `utils.cppm` or `utils.hpp` to measure intermediate library rebuild costs.
- `touch-cpp`: touches `RayTracing.cpp` to measure turnaround time on a leaf translation unit.

### Re-generating reports from saved data

Generate reports, plots, and console summaries from existing JSON data without rebuilding:

```bash
uv run scripts/benchmark_cpp20_modules.py --import-data out/benchmark/benchmark_data.json
```

Import measurements from terminal logs:

```bash
uv run scripts/benchmark_cpp20_modules.py --import-log build.log
```

## Command-line options

| Option | Default | Description |
| :--- | :--- | :--- |
| `--runs <N>` | `10` | Number of iterations per scenario |
| `-j, --jobs <N>` | Ninja default | Parallel build jobs |
| `--configs <list>` | `modules pch headers` | Configurations to benchmark |
| `--scenarios <list>` | All scenarios | Scenarios to benchmark |
| `--cmake <path>` | `cmake` | Path to CMake binary |
| `--clang <path>` | `clang++` | Path to Clang++ compiler |
| `--build-base <dir>` | `out/build` | Directory containing build trees |
| `--report-dir <dir>` | `out/benchmark` | Directory for reports, plots, and traces |
| `--import-data <file>` | None | Load measurements from JSON and skip builds |
| `--export-data <file>` | `out/benchmark/benchmark_data.json` | Save measurements to JSON |
| `--import-log <file>` | None | Load measurements from raw build log |

## Generated artifacts

All artifacts are written to `--report-dir` (default: `out/benchmark/`):

- `benchmark_report.html`: HTML report containing summary tables, compiler phase breakdowns, and speedup ratios.
- `benchmark_data.json`: serialized raw timing measurements across iterations.
- `plots/`: vector SVG charts:
  - `scenarios_compiler_time_box_plot.svg`: grouped box-and-whisker plot for compiler CPU time.
  - `scenarios_wall_time_box_plot.svg`: grouped box-and-whisker plot for wall-clock duration.
  - `compiler_phase_breakdown_bar_plot.svg`: stacked bar chart of frontend parsing vs backend code generation.
- `traces/`:
  - `modules_RayTracing.cpp.json`, `pch_RayTracing.cpp.json`, `headers_RayTracing.cpp.json`: Clang `-ftime-trace` compiler profiles.
  - `ninjatrace_*_augmented.json`: multi-threaded Ninja build timelines with embedded compiler slices.

## Trace inspection

### Flame graphs with Speedscope

1. Open [speedscope.app](https://www.speedscope.app/) in a browser.
2. Drag and drop any `.json` file from `out/benchmark/traces/`.
3. To inspect over local HTTP without manual file upload:

   ```bash
   uv run python -m http.server 8000 --directory out/benchmark/traces
   ```

   Open: `https://www.speedscope.app/#profileURL=http://localhost:8000/ninjatrace_modules_augmented.json`

### Waterfall timelines with Perfetto

1. Open [ui.perfetto.dev](https://ui.perfetto.dev/) in a browser.
2. Select **Open trace file** and choose any `.json` from `out/benchmark/traces/`.
3. Inspect parallel worker thread scheduling and drill down into compilation steps.
