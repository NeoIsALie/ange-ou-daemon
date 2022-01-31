import typing as t

_TAccessorValue = t.TypeVar("_TAccessorValue")


class _DictAccessorProperty(t.Generic[_TAccessorValue]):
    """Baseclass for `environ_property` and `header_property`."""

    read_only = False

    def __init__(
            self,
            name: str,
            default: t.Optional[_TAccessorValue] = None,
            load_func: t.Optional[t.Callable[[str], _TAccessorValue]] = None,
            dump_func: t.Optional[t.Callable[[_TAccessorValue], str]] = None,
            read_only: t.Optional[bool] = None,
            doc: t.Optional[str] = None,
    ) -> None:
        self.name = name
        self.default = default
        self.load_func = load_func
        self.dump_func = dump_func
        if read_only is not None:
            self.read_only = read_only
        self.__doc__ = doc

    def lookup(self, instance: t.Any) -> t.MutableMapping[str, t.Any]:
        raise NotImplementedError

    @t.overload
    def __get__(
            self, instance: None, owner: type
    ) -> "_DictAccessorProperty[_TAccessorValue]":
        ...

    @t.overload
    def __get__(self, instance: t.Any, owner: type) -> _TAccessorValue:
        ...

    def __get__(
            self, instance: t.Optional[t.Any], owner: type
    ) -> t.Union[_TAccessorValue, "_DictAccessorProperty[_TAccessorValue]"]:
        if instance is None:
            return self

        storage = self.lookup(instance)

        if self.name not in storage:
            return self.default  

        value = storage[self.name]

        if self.load_func is not None:
            try:
                return self.load_func(value)
            except (ValueError, TypeError):
                return self.default  

        return value  

    def __set__(self, instance: t.Any, value: _TAccessorValue) -> None:
        if self.read_only:
            raise AttributeError("read only property")

        if self.dump_func is not None:
            self.lookup(instance)[self.name] = self.dump_func(value)
        else:
            self.lookup(instance)[self.name] = value

    def __delete__(self, instance: t.Any) -> None:
        if self.read_only:
            raise AttributeError("read only property")

        self.lookup(instance).pop(self.name, None)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"


class header_property(_DictAccessorProperty[_TAccessorValue]):
    """Like `environ_property` but for headers."""

    def lookup(self, obj: t.Union["Request", "Response"]):
        return obj.headers
