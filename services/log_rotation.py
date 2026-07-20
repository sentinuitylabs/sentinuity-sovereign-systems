"""
log_rotation.py — called by services on startup
Rotates logs over 10MB, keeps last 3 rotations
"""
import logging, logging.handlers
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

def get_rotating_logger(service_name: str, max_mb: int = 10, backup_count: int = 2):
    """
    Returns a logger with rotation at max_mb.
    Keeps backup_count old files then deletes.
    10MB × 3 files = 30MB max per service.
    """
    log_file = LOG_DIR / f"{service_name}.log"
    handler  = logging.handlers.RotatingFileHandler(
        str(log_file),
        maxBytes   = max_mb * 1_048_576,
        backupCount= backup_count,
        encoding   = "utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s - [%(name)s] %(levelname)-7s %(message)s"
    ))
    logger = logging.getLogger(service_name)
    if not logger.handlers:
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
