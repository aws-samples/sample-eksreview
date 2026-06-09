"""Logging setup with structured output for agent operations."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from eks_review_agent.config import LOG_LEVEL

# debug.log can contain cluster names, IAM ARNs, resource lists, and tool
# parameters. Default the file handler to INFO and add rotation so the file
# does not grow unbounded across sessions. Set LOG_LEVEL=DEBUG explicitly to
# capture full diagnostic detail.
_LOG_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
_LOG_FILE_BACKUP_COUNT = 3              # keep debug.log + .1 + .2 + .3


def setup_logging() -> logging.Logger:
    """Configure logging for the EKS review agent.

    - Console (stderr): respects LOG_LEVEL.
    - File (debug.log): rotated at 10 MB, 3 backups, level matches LOG_LEVEL
      (so DEBUG only when explicitly requested). Reduces accidental retention
      of sensitive cluster data on disk.

    Returns:
        The configured agent logger.
    """
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    # Never go below INFO for the file unless the user opted into DEBUG.
    file_level = logging.DEBUG if level == logging.DEBUG else max(level, logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console handler (stderr) — respects LOG_LEVEL
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    # Rotating file handler — bounded size, level matches LOG_LEVEL
    log_file = Path("debug.log")
    file_handler = RotatingFileHandler(
        str(log_file),
        mode="a",
        maxBytes=_LOG_FILE_MAX_BYTES,
        backupCount=_LOG_FILE_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    file_handler.setLevel(file_level)

    # Agent logger
    logger = logging.getLogger("eksreview")
    logger.setLevel(min(level, file_level))  # Let handlers decide what to emit
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.propagate = False

    # Strands SDK logger — same file/console levels as the agent logger
    strands_logger = logging.getLogger("strands")
    strands_logger.setLevel(min(level, file_level))
    strands_logger.addHandler(file_handler)
    strands_console = logging.StreamHandler(sys.stderr)
    strands_console.setFormatter(formatter)
    strands_console.setLevel(logging.DEBUG if level == logging.DEBUG else logging.WARNING)
    strands_logger.addHandler(strands_console)
    strands_logger.propagate = False

    logger.info(
        "Logging to file: %s (level=%s, rotation=%dMB x %d)",
        log_file.absolute(),
        logging.getLevelName(file_level),
        _LOG_FILE_MAX_BYTES // (1024 * 1024),
        _LOG_FILE_BACKUP_COUNT,
    )

    return logger
