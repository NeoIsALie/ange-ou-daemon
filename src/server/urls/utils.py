import typing as t

from src.server.urls.urls import (
    uri_to_iri,
    url_quote
)


def get_host(
        scheme: str,
        host_header: t.Optional[str],
        server: t.Optional[t.Tuple[str, t.Optional[int]]] = None
) -> str:
    """Return the host for the given parameters.

    This first checks the ``host_header``. If it's not present, then
    ``server`` is used. The host will only contain the port if it is
    different than the standard port for the protocol.

    :param scheme: The protocol the request used, like ``"https"``.
    :param host_header: The ``Host`` header value.
    :param server: Address of the server. ``(host, port)``, or
        ``(path, None)`` for unix sockets.
    :param trusted_hosts: A list of trusted host names.

    :return: Host, with port if necessary.
    """
    host = ""

    if host_header is not None:
        host = host_header
    elif server is not None:
        host = server[0]

        if server[1] is not None:
            host = f"{host}:{server[1]}"

    if scheme in {"http", "ws"} and host.endswith(":80"):
        host = host[:-3]
    elif scheme in {"https", "wss"} and host.endswith(":443"):
        host = host[:-4]

    return host


def get_current_url(
        scheme: str,
        host: str,
        root_path: t.Optional[str] = None,
        path: t.Optional[str] = None,
        query_string: t.Optional[bytes] = None,
) -> str:
    """Recreate the URL for a request.

    :param scheme: The protocol the request used, like ``"https"``.
    :param host: The host the request was made to. See :func:`get_host`.
    :param root_path: Prefix that the application is mounted under. This
        is prepended to ``path``.
    :param path: The path part of the URL after ``root_path``.
    :param query_string: The portion of the URL after the "?".
    """
    url = [scheme, "://", host]

    if root_path is None:
        url.append("/")
        return uri_to_iri("".join(url))

    url.append(url_quote(root_path.rstrip("/")))
    url.append("/")

    if path is None:
        return uri_to_iri("".join(url))

    url.append(url_quote(path.lstrip("/")))

    if query_string:
        url.append("?")
        url.append(url_quote(query_string, safe=":&%=+$!*'(),"))

    return uri_to_iri("".join(url))
