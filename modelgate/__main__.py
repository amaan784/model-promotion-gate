"""Command line entry point: python -m modelgate <command>.

The Makefile targets are thin wrappers around these commands, so the same
steps run where make is not installed (python:3.11-slim, Windows).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable

from modelgate import data, evaluate, scenarios, train
from modelgate.common import Paths, default_paths

logger = logging.getLogger("modelgate")

Step = Callable[[Paths], object]

STEPS: dict[str, Step] = {
    "data": data.main,
    "train": train.main,
    "metrics": evaluate.evaluate_challenger,
    "gate": evaluate.gate,
}
PIPELINES: dict[str, tuple[str, ...]] = {
    "evaluate": ("metrics", "gate"),
    "reports": ("data", "train", "metrics", "gate"),
}


def run_steps(paths: Paths, names: tuple[str, ...]) -> None:
    timings: list[tuple[str, float]] = []
    for name in names:
        started = time.perf_counter()
        STEPS[name](paths)
        timings.append((name, time.perf_counter() - started))
    if len(timings) > 1:
        logger.info("timing summary")
        for name, seconds in timings:
            logger.info("  %-10s %6.2fs", name, seconds)
        logger.info("  %-10s %6.2fs", "total", sum(s for _, s in timings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m modelgate", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (*STEPS, *PIPELINES):
        sub.add_parser(name)
    scenario = sub.add_parser("scenario", help="edit config/train.yaml for a demo scenario")
    scenario.add_argument("name", choices=sorted(scenarios.SCENARIOS))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    paths = default_paths()
    if args.command == "scenario":
        scenarios.apply(paths, args.name)
    elif args.command in PIPELINES:
        run_steps(paths, PIPELINES[args.command])
    else:
        run_steps(paths, (args.command,))
    return 0


if __name__ == "__main__":
    sys.exit(main())
