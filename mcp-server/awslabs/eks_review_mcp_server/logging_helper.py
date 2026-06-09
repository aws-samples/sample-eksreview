# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN
# AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""Logging helper for the EKS Review MCP Server."""

from enum import Enum
from loguru import logger
from mcp.server.fastmcp import Context
from typing import Any


class LogLevel(Enum):
    """Enum for log levels."""

    DEBUG = 'debug'
    INFO = 'info'
    WARNING = 'warning'
    ERROR = 'error'
    CRITICAL = 'critical'


def log_with_request_id(ctx: Context, level: LogLevel, message: str, **kwargs: Any) -> None:
    """Log a message with the request ID from the context.

    Args:
        ctx: The MCP context containing the request ID
        level: The log level (from LogLevel enum)
        message: The message to log
        **kwargs: Additional fields to include in the log message
    """
    # Format the log message with request_id
    log_message = f'[request_id={ctx.request_id}] {message}'

    # Log at the appropriate level
    if level == LogLevel.DEBUG:
        logger.debug(log_message, **kwargs)
    elif level == LogLevel.INFO:
        logger.info(log_message, **kwargs)
    elif level == LogLevel.WARNING:
        logger.warning(log_message, **kwargs)
    elif level == LogLevel.ERROR:
        logger.error(log_message, **kwargs)
    elif level == LogLevel.CRITICAL:
        logger.critical(log_message, **kwargs)