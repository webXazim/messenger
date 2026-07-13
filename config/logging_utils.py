import contextvars
import logging

_request_id_var = contextvars.ContextVar("request_id", default="-")


def set_request_id(value: str) -> None:
    _request_id_var.set(value or "-")


def clear_request_id() -> None:
    _request_id_var.set("-")


def get_request_id() -> str:
    return _request_id_var.get() or "-"


class RequestIDLogFilter(logging.Filter):
    def filter(self, record):
        record.request_id = get_request_id()
        return True
