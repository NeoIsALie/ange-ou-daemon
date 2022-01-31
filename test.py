"""All uploaded files are directly send back to the client."""
import os
from http import HTTPStatus
import typing as t
from src.hash.gost import GOST341112
from src.server.file_ops import wrap_file
from src.server.serving import run_simple
from src.server.requests.request_extended import Request
from src.server.requests.response import Response

from src.server.utils import send_file

MAIN_HTML = """
        <h1>Ange Ou Daemon</h1>
        <h2 Загрузка </h2>
        <form action="" method="post" enctype="multipart/form-data">
            <input type="file" name="uploaded_file">
            <input type="submit" value="Upload">
        </form>
        <h2 Скачивание </h2>
        <form action="" method="get">
            <input type="text" name="hash_download">
            <input type="submit" value="Download">
        </form>  
        <h2 Удаление </h2>
        <form action="" method="get">
            <input type="text" name="hash_delete">
            <input type="submit" value="Delete">
        </form>    
"""


def calculate_hash(filename: t.Any):
    gost_hash = GOST341112()
    if isinstance(filename, str):
        with open(filename, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                gost_hash.update(chunk)
    else:
        for chunk in filename:
            gost_hash.update(chunk)
    return gost_hash.hexdigest()


def upload_file(req):
    if "uploaded_file" not in req.files:
        return Response(response="no file uploaded", status=HTTPStatus.BAD_REQUEST)
    f = req.files["uploaded_file"]
    hash_upload = calculate_hash(wrap_file(req.environ, f))
    f.seek(0)
    dir_name = hash_upload[:2]
    try:
        path = os.path.dirname(__file__) + f"/store/{dir_name}"
        if not os.path.isdir(path):
            os.mkdir(path)
        f.save(os.path.join(path, f.filename))
        return Response(response=f"File hash: {hash_upload}", status=HTTPStatus.OK)
    except OSError:
        return Response(status=HTTPStatus.UNAVAILABLE_FOR_LEGAL_REASONS)


def download_file(req):
    hash_download = req.query_string.decode().replace("hash_download=", "")
    if hash_download is None or hash_download == "":
        return Response(response="No hash provided", status=HTTPStatus.BAD_REQUEST)
    dir_name = hash_download[:2]
    path = os.path.dirname(__file__) + f"/store/{dir_name}"
    for filename in os.listdir(path):
        curr_hash = calculate_hash(os.path.join(path, filename))
        if hash_download == curr_hash:
            try:
                return send_file(path_or_file=os.path.join(path, filename),
                                 environ=req.environ,
                                 response_class=Response,
                                 as_attachment=True
                                 )
            except OSError:
                return Response(response="Can not download file", status=HTTPStatus.UNAVAILABLE_FOR_LEGAL_REASONS)
    return Response(response="No file with such hash", status=HTTPStatus.BAD_REQUEST)


def delete_file(req):
    hash_delete = req.query_string.decode().replace("hash_delete=", "")
    if hash_delete is None or hash_delete == "":
        return Response(response="No hash provided", status=HTTPStatus.BAD_REQUEST)
    dir_name = hash_delete[:2]
    path = os.path.dirname(__file__) + f"/store/{dir_name}"
    for filename in os.listdir(path):
        curr_hash = calculate_hash(os.path.join(path, filename))
        if hash_delete == curr_hash:
            try:
                os.remove(os.path.join(path, filename))
                return Response(response="Successful file delete", status=HTTPStatus.OK)
            except OSError:
                return Response(response="Can not delete file", status=HTTPStatus.UNAVAILABLE_FOR_LEGAL_REASONS)
    return Response(response="No file with such hash", status=HTTPStatus.BAD_REQUEST)


def main_view(req):
    return Response(
        MAIN_HTML,
        mimetype="text/html",
    )


def application(environ, start_response):
    req = Request(environ)
    if req.method == "POST":
        resp = upload_file(req)
    elif "hash_delete" in req.query_string.decode():
        resp = delete_file(req)
    elif "hash_download" in req.query_string.decode():
        resp = download_file(req)
    else:
        resp = main_view(req)
    return resp(environ, start_response)


if __name__ == "__main__":
    run_simple("localhost", 5000, application)
