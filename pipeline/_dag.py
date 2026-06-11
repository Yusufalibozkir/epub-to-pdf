"""
Lightweight directed-acyclic-graph (DAG) pipeline executor.

Each major processing stage is a node with named dependencies. The executor
topologically sorts the graph, checks the content-addressed cache for each
node, and only runs stages whose inputs have changed.

Real-time progress is printed to stderr:

    [1/12] Reading EPUB assets... ✓ 1.2s  (cached)
    [2/12] Classifying documents... ✓ 3.5s
"""
from __future__ import annotations

import enum
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class StageStatus(enum.Enum):
    PENDING = "pending"
    CACHED = "cached"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Stage:
    """One node in the pipeline DAG.

    Attributes:
        name:        Unique stage identifier.
        depends_on:  Names of stages that must complete before this one.
        runner:      Callable(context: PipelineContext) -> dict[str, Any].
                     Returns a dict of output keys -> values to store in context.data.
        cache_key:   Optional callable(context) -> str | None.
                     If None, the stage always runs.
                     If it returns a string key, the cache is checked first.
        description: Human-readable purpose (for logging/progress).
    """
    name: str
    depends_on: list[str] = field(default_factory=list)
    runner: Optional[Callable] = None
    cache_key: Optional[Callable] = None
    description: str = ""


@dataclass
class PipelineContext:
    """Mutable context passed through all stages during one pipeline run.

    data holds all produced values, keyed by name (e.g. 'fragments', 'css', 'verdict').
    """
    data: dict[str, Any] = field(default_factory=dict)
    settings: Any = None
    log: Any = None
    args: Any = None
    cache: Any = None
    epub_path: Any = None
    out_pdf: Any = None
    artifact_dir: Any = None
    build_dir: Any = None


class PipelineDAG:
    """A directed acyclic graph of processing stages.

    Usage:
        dag = PipelineDAG()
        dag.add_stage(Stage("read_epub", runner=read_epub, ...))
        dag.add_stage(Stage("scan_classify", depends_on=["read_epub"], ...))
        dag.run(context)
    """

    def __init__(self):
        self._stages: dict[str, Stage] = {}

    def add_stage(self, stage: Stage) -> "PipelineDAG":
        if stage.name in self._stages:
            raise ValueError(f"Duplicate stage name: {stage.name}")
        self._stages[stage.name] = stage
        return self

    @property
    def stage_names(self) -> list[str]:
        return list(self._stages.keys())

    # ------------------------------------------------------------------
    # Topological sort (Kahn's algorithm)
    # ------------------------------------------------------------------

    def _topological_order(self) -> list[str]:
        """Return stage names in dependency order."""
        in_degree: dict[str, int] = {n: 0 for n in self._stages}
        adjacency: dict[str, list[str]] = {n: [] for n in self._stages}

        for name, stage in self._stages.items():
            for dep in stage.depends_on:
                if dep not in self._stages:
                    raise ValueError(
                        f"Stage '{name}' depends on unknown stage '{dep}'"
                    )
                adjacency[dep].append(name)
                in_degree[name] += 1

        queue = [n for n, d in in_degree.items() if d == 0]
        order = []

        while queue:
            node = queue.pop(0)
            order.append(node)
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(order) != len(self._stages):
            raise ValueError(
                "Pipeline DAG contains a cycle. Stages: "
                + ", ".join(f"{s}({in_degree.get(s, 0)})" for s in self._stages)
            )
        return order

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(self, ctx: PipelineContext) -> dict[str, Any]:
        """Execute all stages in dependency order, respecting the cache.

        Prints real-time progress to stderr:

            [1/12] Reading EPUB assets... ✓ 1.2s  (cached)

        Returns ctx.data (all produced values).
        """
        order = self._topological_order()
        total = len(order)
        stage_statuses: dict[str, StageStatus] = {}

        for name in order:
            stage = self._stages[name]
            stage_statuses[name] = StageStatus.PENDING

        for idx, name in enumerate(order, start=1):
            stage = self._stages[name]
            log = ctx.log
            desc = stage.description or name.replace("_", " ").title()

            # --- Cache check ---
            cache_hit = False
            cache_namespace = f"dag_{name}"
            cache_key_value: Optional[str] = None

            if stage.cache_key is not None and ctx.cache is not None:
                try:
                    cache_key_value = stage.cache_key(ctx)
                except Exception:
                    cache_key_value = None

                if cache_key_value is not None and ctx.cache.has(cache_namespace, cache_key_value):
                    cached_data = ctx.cache.load(cache_namespace, cache_key_value)
                    if cached_data is not None and isinstance(cached_data, dict):
                        ctx.data.update(cached_data)
                        cache_hit = True
                        stage_statuses[name] = StageStatus.CACHED
                        if log:
                            log.warn(f"[DAG]  CACHED  {name}  (from cache)")

            # --- Print status line ---
            pad = len(str(total))
            if cache_hit:
                print(f"[{idx:>{pad}}/{total}] {desc}... ✓ (cached)", file=sys.stderr)
                continue

            # --- Execute ---
            if stage.runner is None:
                raise ValueError(f"Stage '{name}' has no runner and no cached result.")
            stage_statuses[name] = StageStatus.RUNNING
            print(f"[{idx:>{pad}}/{total}] {desc}... ", file=sys.stderr, end="", flush=True)
            if log:
                log_desc = f"  ({stage.description})" if stage.description else ""
                log.warn(f"[DAG]  RUNNING {name}{log_desc}")
            t0 = time.perf_counter()
            try:
                outputs = stage.runner(ctx)
            except Exception as exc:
                stage_statuses[name] = StageStatus.FAILED
                elapsed = time.perf_counter() - t0
                print(f"\b✗ {elapsed:.1f}s", file=sys.stderr)
                raise RuntimeError(f"Stage '{name}' failed: {exc}") from exc

            elapsed = time.perf_counter() - t0
            if outputs is not None and isinstance(outputs, dict):
                ctx.data.update(outputs)

            # --- Store in cache ---
            if cache_key_value is not None and ctx.cache is not None and outputs is not None:
                try:
                    ctx.cache.store(cache_namespace, cache_key_value, outputs)
                except Exception as exc:
                    if log:
                        log.warn(f"[DAG]  Cache write failed for '{name}': {exc}")

            stage_statuses[name] = StageStatus.COMPLETED
            print(f"✓ {elapsed:.1f}s", file=sys.stderr)

        return ctx.data
