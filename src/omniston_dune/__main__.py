from __future__ import annotations

import argparse
import logging
import sys

from . import config, dune, omniston, pipeline


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="omniston-dune",
        description="Refresh Omniston history tables in Dune.",
    )
    parser.add_argument(
        "--query-ids",
        default="",
        help="Comma-separated Dune query IDs to execute after uploading, so the "
             "dashboard's cached results refresh.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report row counts without writing anything to Dune.",
    )
    args = parser.parse_args(argv)
    args.query_ids = [int(part) for part in args.query_ids.split(",") if part.strip()]
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        settings = config.load_settings()

        if args.dry_run:
            datasets = pipeline.build_datasets(settings)
            for table_name, rows in datasets.items():
                logging.info("%s: %d rows (dry run, nothing written)", table_name, len(rows))
            # Validate even though nothing will be written: an empty table, a
            # gap in day coverage or a null in a non-nullable column are
            # precisely the problems a dry run exists to surface, and skipping
            # the check left it unable to find any of them.
            pipeline.validate_datasets(datasets)
            return 0

        written = pipeline.run(settings, query_ids=args.query_ids)
        for table_name, count in written.items():
            logging.info("%s: %d rows written", table_name, count)
        return 0
    except (config.ConfigError, pipeline.PublishError, dune.DuneError, omniston.OmnistonError) as exc:
        # These are the project's own known error types, each already carrying
        # a message written for an operator (config.ConfigError especially --
        # it names the missing setting and how to fix it). Without this catch
        # that guidance is buried inside a raw traceback. Any other exception
        # is unexpected and keeps propagating with its traceback intact: an
        # unexpected error is a bug, and the traceback is the evidence.
        logging.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
