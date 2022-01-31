import typing as t
from functools import partial


class ClosingIterator:
    """The WSGI specification requires that all middlewares and gateways
    respect the `close` callback of the iterable returned by the application.
    """

    def __init__(
        self,
        iterable: t.Iterable[bytes],
        callbacks: t.Optional[
            t.Union[t.Callable[[], None], t.Iterable[t.Callable[[], None]]]
        ] = None,
    ) -> None:
        iterator = iter(iterable)
        self._next = t.cast(t.Callable[[], bytes], partial(next, iterator))
        if callbacks is None:
            callbacks = []
        elif callable(callbacks):
            callbacks = [callbacks]
        else:
            callbacks = list(callbacks)
        iterable_close = getattr(iterable, "close", None)
        if iterable_close:
            callbacks.insert(0, iterable_close)
        self._callbacks = callbacks

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        return self._next()

    def close(self) -> None:
        for callback in self._callbacks:
            callback()