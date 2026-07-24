"""REAL logging for the live workbench backend — stdout + a ROLLING file, so a run is
traceable end-to-end and the 500s we saw land in a file WITH a full stack (not just uvicorn's
stderr). Configured ONCE at server startup (`api/server.create_server`, BEFORE the manager is
built); every module logs through the `iw_engine` logger tree (`logging.getLogger(__name__)`),
so a child's records propagate to the two handlers set up here.

    from iw_engine.runtime.logging_setup import setup_logging
    setup_logging()                          # idempotent — safe to call more than once
    logging.getLogger(__name__).info("...")  # any iw_engine.* child logs to stdout + the file

Level is `IW_LOG_LEVEL` (default INFO); the file rolls at ~2MB x 5 backups under
`<IW_DATA_ROOT or engine/data>/logs/iw-engine.log` (mirrors `InvestigationStore`'s data root).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import pathlib
import sys

import iw_engine

_LOGGER_NAME = "iw_engine"
# timestamp + level + logger + message — concise, one line, greppable.
_FORMAT = "%(asctime)s %(levelname)-5s %(name)s %(message)s"
_MAX_BYTES = 2 * 1024 * 1024   # ~2MB per file before it rolls
_BACKUP_COUNT = 5              # keep 5 rolled backups (iw-engine.log.1 .. .5)


def _log_dir() -> pathlib.Path:
    """`<IW_DATA_ROOT or engine/data>/logs` — an explicit `IW_DATA_ROOT` wins (a deployment sets
    it); the package-relative fallback (`iw_engine.__file__ -> parents[2]` = the in-repo `engine/`
    dir) mirrors `InvestigationStore._default_root`, so logs land beside `data/investigations/`."""
    base = os.environ.get("IW_DATA_ROOT")
    root = (pathlib.Path(base) if base
            else pathlib.Path(iw_engine.__file__).resolve().parents[2] / "data")
    return root / "logs"


def setup_logging() -> logging.Logger:
    """Configure the `iw_engine` logger with a stdout `StreamHandler` AND a
    `RotatingFileHandler`. IDEMPOTENT: our handlers are tagged (`_iw_kind`), so a second call
    never double-adds them — it only re-reads the level (so a level change on restart takes
    effect). `propagate=False` keeps the iw_engine tree off root's last-resort handler (no
    double-log, no capturing third-party noise). Returns the configured logger."""
    level_name = os.environ.get("IW_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False   # our handlers own the iw_engine tree

    installed = {getattr(h, "_iw_kind", None) for h in logger.handlers}
    formatter = logging.Formatter(_FORMAT)

    if "stream" not in installed:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(formatter)
        sh._iw_kind = "stream"   # tag so a re-call is idempotent
        logger.addHandler(sh)

    if "file" not in installed:
        log_dir = _log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_dir / "iw-engine.log", maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
            encoding="utf-8")
        fh.setFormatter(formatter)
        fh._iw_kind = "file"
        logger.addHandler(fh)

    for h in logger.handlers:      # re-reads the level so a restart's IW_LOG_LEVEL change applies
        h.setLevel(level)
    return logger
