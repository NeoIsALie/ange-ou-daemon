import http.client
import json
import os
import shutil
import socket
from pathlib import Path

import pytest

from src.server.serving import run_simple


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({}, id="http"),
        pytest.param({"ssl_context": "adhoc"}, id="https"),
        pytest.param({"use_reloader": True}, id="reloader"),
        pytest.param(
            {"hostname": "unix"},
            id="unix socket",
            marks=pytest.mark.skipif(
                not hasattr(socket, "AF_UNIX"), reason="requires unix socket support"
            ),
        ),
    ],
)
def test_server(tmp_path, dev_server, kwargs: dict):
    if kwargs.get("hostname") == "unix":
        kwargs["hostname"] = f"unix://{tmp_path / 'test.sock'}"

    client = dev_server(**kwargs)
    r = client.request()
    assert r.status == 200
    assert r.json["PATH_INFO"] == "/"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_double_slash_path(standard_app):
    r = standard_app.request("//double-slash")
    assert "double-slash" not in r.json["HTTP_HOST"]
    assert r.json["PATH_INFO"] == "/double-slash"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_500_error(standard_app):
    r = standard_app.request("/crash")
    assert r.status == 500
    assert b"Internal Server Error" in r.data


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_ssl_object(dev_server):
    client = dev_server(ssl_context="custom")
    r = client.request()
    assert r.json["wsgi.url_scheme"] == "https"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
@pytest.mark.parametrize("reloader_type", ["stat", "watchdog"])
@pytest.mark.skipif(
    os.name == "nt" and "CI" in os.environ, reason="unreliable on Windows during CI"
)
def test_reloader_sys_path(tmp_path, dev_server, reloader_type):
    """This tests the general behavior of the reloader. It also tests
    that fixing an import error triggers a reload, not just Python
    retrying the failed import.
    """
    real_path = tmp_path / "real_app.py"
    real_path.write_text("syntax error causes import error")

    client = dev_server("reloader", reloader_type=reloader_type)
    assert client.request().status == 500

    shutil.copyfile(Path(__file__).parent / "live_apps" / "standard_app.py", real_path)
    client.wait_for_log(f" * Detected change in {str(real_path)!r}, reloading")
    client.wait_for_reload()
    assert client.request().status == 200


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_content_type_and_length(standard_app):
    r = standard_app.request()
    assert "CONTENT_TYPE" not in r.json
    assert "CONTENT_LENGTH" not in r.json

    r = standard_app.request(body=b"{}", headers={"content-type": "application/json"})
    assert r.json["CONTENT_TYPE"] == "application/json"
    assert r.json["CONTENT_LENGTH"] == "2"


def test_port_is_int():
    with pytest.raises(TypeError, match="port must be an integer"):
        run_simple("127.0.0.1", "5000", None)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_multiple_headers_concatenated(standard_app):
    """A header key can be sent multiple times. The server will join all
    the values with commas.

    https://tools.ietf.org/html/rfc3875#section-4.1.18
    """
    # conn.request doesn't support multiple values.
    conn = standard_app.connect()
    conn.putrequest("GET", "/")
    conn.putheader("XYZ", "a ")  # trailing space is preserved
    conn.putheader("X-Ignore-1", "ignore value")
    conn.putheader("XYZ", " b")  # leading space is collapsed
    conn.putheader("X-Ignore-2", "ignore value")
    conn.putheader("XYZ", "c ")
    conn.putheader("X-Ignore-3", "ignore value")
    conn.putheader("XYZ", "d")
    conn.endheaders()
    r = conn.getresponse()
    data = json.load(r)
    r.close()
    assert data["HTTP_XYZ"] == "a ,b,c ,d"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_multiline_header_folding(standard_app):
    """A header value can be split over multiple lines with a leading
    tab. The server will remove the newlines and preserve the tabs.

    https://tools.ietf.org/html/rfc2616#section-2.2
    """
    # conn.request doesn't support multiline values.
    conn = standard_app.connect()
    conn.putrequest("GET", "/")
    conn.putheader("XYZ", "first", "second", "third")
    conn.endheaders()
    r = conn.getresponse()
    data = json.load(r)
    r.close()
    assert data["HTTP_XYZ"] == "first\tsecond\tthird"


@pytest.mark.parametrize("endpoint", ["", "crash"])
def test_streaming_close_response(dev_server, endpoint):
    """When using HTTP/1.0, chunked encoding is not supported. Fall
    back to Connection: close, but this allows no reliable way to
    distinguish between complete and truncated responses.
    """
    r = dev_server("streaming").request("/" + endpoint)
    assert r.getheader("connection") == "close"
    assert r.data == "".join(str(x) + "\n" for x in range(5)).encode()


def test_streaming_chunked_response(dev_server):
    """When using HTTP/1.1, use Transfer-Encoding: chunked for streamed
    responses, since it can distinguish the end of the response without
    closing the connection.

    https://tools.ietf.org/html/rfc2616#section-3.6.1
    """
    r = dev_server("streaming", threaded=True).request("/")
    assert r.getheader("transfer-encoding") == "chunked"
    assert r.data == "".join(str(x) + "\n" for x in range(5)).encode()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_streaming_chunked_truncation(dev_server):
    """When using HTTP/1.1, chunked encoding allows the client to detect
    content truncated by a prematurely closed connection.
    """
    with pytest.raises(http.client.IncompleteRead):
        dev_server("streaming", threaded=True).request("/crash")
