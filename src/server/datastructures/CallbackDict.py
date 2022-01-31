from src.server.datastructures.Header import _missing


def _calls_update(name):
    def oncall(self, *args, **kw):
        rv = getattr(super(UpdateDictMixin, self), name)(*args, **kw)

        if self.on_update is not None:
            self.on_update(self)

        return rv

    oncall.__name__ = name
    return oncall


class UpdateDictMixin(dict):
    """Makes dicts call `self.on_update` on modifications."""

    on_update = None

    def setdefault(self, key, default=None):
        modified = key not in self
        rv = super().setdefault(key, default)
        if modified and self.on_update is not None:
            self.on_update(self)
        return rv

    def pop(self, key, default=_missing):
        modified = key in self
        if default is _missing:
            rv = super().pop(key)
        else:
            rv = super().pop(key, default)
        if modified and self.on_update is not None:
            self.on_update(self)
        return rv

    __setitem__ = _calls_update("__setitem__")
    __delitem__ = _calls_update("__delitem__")
    clear = _calls_update("clear")
    popitem = _calls_update("popitem")
    update = _calls_update("update")


class CallbackDict(UpdateDictMixin, dict):
    """A dict that calls a function passed every time something is changed.
    The function is passed the dict instance.
    """

    def __init__(self, initial=None, on_update=None):
        dict.__init__(self, initial or ())
        self.on_update = on_update

    def __repr__(self):
        return f"<{type(self).__name__} {dict.__repr__(self)}>"
