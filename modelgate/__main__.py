"""Command line entry point: python -m modelgate <command>.

The Makefile targets are thin wrappers around these commands, so the same
steps run where make is not installed (python:3.11-slim, Windows). Step modules
are imported only when a step runs, so light commands such as `register` do not
load scikit-learn or CodeCarbon.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
import time
from contextlib import nullcontext

from modelgate.common import Paths, default_paths
from modelgate.scenarios import SCENARIOS, apply

logger = logging.getLogger("modelgate")

STEPS: dict[str, str] = {
    "data": "modelgate.data:main",
    "train": "modelgate.train:main",
    "scan": "modelgate.scan:main",
    "metrics": "modelgate.evaluate:evaluate_challenger",
    "sci": "modelgate.carbon:main",
    "gate": "modelgate.evaluate:gate",
    "register": "modelgate.registry:main",
    "promote": "modelgate.promote:main",
    "deploy": "modelgate.promote:deploy",
    "site": "modelgate.site:main",
    "drift": "modelgate.drift:main",
    "codeowners": "modelgate.owners:main",
}
# Steps whose energy is measured by CodeCarbon; scan runs before anything unpickles the model.
MEASURED: tuple[str, ...] = ("data", "train", "scan", "metrics")
PIPELINES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # name: (measured steps, unmeasured steps that follow)
    "evaluate": ((), ("metrics", "gate")),
    "carbon": (MEASURED, ("sci",)),
    "reports": (MEASURED, ("sci", "gate")),
}


def _run(paths: Paths, name: str, timings: list[tuple[str, float]]) -> None:
    """Import the step's module, call its function with paths and record the duration.

    Appends (name, seconds) to timings only when the step returns without raising.
    """
    module, func = STEPS[name].split(":")
    started = time.perf_counter()
    getattr(importlib.import_module(module), func)(paths)
    timings.append((name, time.perf_counter() - started))


def run_pipeline(paths: Paths, measured: tuple[str, ...], after: tuple[str, ...]) -> None:
    """Run the measured steps under one CodeCarbon measurement, then the steps in after.

    When measured is non-empty, carbon.measure wraps those steps and writes
    reports/energy.json, also when a step raises; the steps in after run outside the
    measurement. When measured is empty, a null context is used and modelgate.carbon is not
    imported here. When more than one step completed, logs each step's duration in seconds,
    the remaining elapsed time as codecarbon (only when steps were measured) and the total.
    Exceptions raised by a step or by the measurement are not caught, and no timing summary
    is logged in that case.
    """
    timings: list[tuple[str, float]] = []
    started = time.perf_counter()
    if measured:
        from modelgate.carbon import measure

        context = measure(paths)
    else:
        context = nullcontext()
    with context:
        for name in measured:
            _run(paths, name, timings)
    for name in after:
        _run(paths, name, timings)
    if len(timings) > 1:
        logger.info("timing summary")
        for name, seconds in timings:
            logger.info("  %-10s %6.2fs", name, seconds)
        overhead = time.perf_counter() - started - sum(s for _, s in timings)
        if measured:
            logger.info("  %-10s %6.2fs", "codecarbon", overhead)
        logger.info("  %-10s %6.2fs", "total", time.perf_counter() - started)


def main(argv: list[str] | None = None) -> int:
    """Parse the command line, run the chosen step, pipeline or scenario and return an exit code.

    Configures INFO logging and takes paths from default_paths(). A step name runs that step
    alone without energy measurement, a PIPELINES name runs its steps through run_pipeline,
    and `scenario <name>` writes that scenario's feature list into config/train.yaml and
    returns 0. Returns 1 after logging the error when the step or pipeline raises
    RuntimeError, and 0 when it completes. Other exceptions propagate, and argparse exits
    with status 2 on invalid arguments.
    """
    parser = argparse.ArgumentParser(prog="python -m modelgate", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (*STEPS, *PIPELINES):
        sub.add_parser(name)
    scenario = sub.add_parser("scenario", help="edit config/train.yaml for a demo scenario")
    scenario.add_argument("name", choices=sorted(SCENARIOS))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    paths = default_paths()
    if args.command == "scenario":
        apply(paths, args.name)
        return 0
    try:
        if args.command in PIPELINES:
            run_pipeline(paths, *PIPELINES[args.command])
        else:
            run_pipeline(paths, (), (args.command,))
    except RuntimeError as exc:  # step failures that should end the job with a message
        logger.error("%s failed: %s", args.command, exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
