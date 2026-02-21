import logging

import click

LOG_FORMAT = '\t%(levelname)s: %(asctime)s [%(module)s] %(message)s'
DATE_FORMAT = '%H:%M:%S'

LEVEL_COLORS = {
    'DEBUG': 'cyan',
    'INFO': 'green',
    'WARNING': 'yellow',
    'ERROR': 'red',
    'CRITICAL': 'bright_red',
}


class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname
        time = self.formatTime(record, self.datefmt)
        module = record.name.rsplit('.', 1)[-1]
        message = record.getMessage()

        colored_level = click.style(
            f'{level}:', fg=LEVEL_COLORS.get(level), bold=True
        )

        # Check for color hint in record
        msg_color = getattr(record, 'color', 'bright_white')
        colored_msg = click.style(message, fg=msg_color)

        return f'\t{colored_level} {time} [{module}] {colored_msg}'


class Logger(logging.Logger):
    def blue(self, msg: str, *args, **kwargs) -> None:
        """Log with blue text."""
        if self.isEnabledFor(logging.INFO):
            kwargs['extra'] = {'color': 'blue'}
            self._log(logging.INFO, msg, args, **kwargs)

    def cyan(self, msg: str, *args, **kwargs) -> None:
        """Log with cyan/light blue text."""
        if self.isEnabledFor(logging.INFO):
            kwargs['extra'] = {'color': 'cyan'}
            self._log(logging.INFO, msg, args, **kwargs)

    def yellow(self, msg: str, *args, **kwargs) -> None:
        """Log with yellow text."""
        if self.isEnabledFor(logging.INFO):
            kwargs['extra'] = {'color': 'yellow'}
            self._log(logging.INFO, msg, args, **kwargs)


def get_logger(name: str) -> Logger:
    """Get a logger that matches uvicorn/FastAPI log format."""
    logging.setLoggerClass(Logger)
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(ColorFormatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger  # type: ignore
