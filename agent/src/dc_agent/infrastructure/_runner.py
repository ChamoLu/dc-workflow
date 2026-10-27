"""Isolated runner entry point. Download mocks are test controls, not a sandbox."""

from __future__ import annotations

import contextlib
import io
import json
import socket
import sys
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlsplit


class DownloadDenied(RuntimeError):
    pass


class BoundedLog(io.TextIOBase):
    def __init__(self, limit=16384):
        self.parts = []
        self.remaining = limit

    def write(self, text):
        if self.remaining:
            part = str(text)[: self.remaining]
            self.parts.append(part)
            self.remaining -= len(part)
        return len(text)

    def flush(self):
        pass


def install_download_mock(source_bytes: bytes, source_id: str):
    def check(method, url):
        parsed = urlsplit(str(url))
        if (
            str(method).upper() != "GET"
            or parsed.scheme != "http"
            or parsed.netloc != "service-1.example.invalid"
            or parsed.query
            or parsed.fragment
            or unquote(parsed.path) != "/api/file/download/" + source_id
        ):
            raise DownloadDenied("Only this task's source download is permitted")

    try:
        import requests

        def request(method, url, *args, **kwargs):
            check(method, url)
            if kwargs.get("params"):
                raise DownloadDenied("Download query parameters are not permitted")
            response = requests.Response()
            response.status_code = 200
            response.url = str(url)
            response._content = source_bytes
            response.raw = io.BytesIO(source_bytes)
            response.headers["Content-Type"] = "application/octet-stream"
            return response

        requests.request = request
        requests.get = lambda url, *a, **kw: request("GET", url, *a, **kw)
        requests.sessions.Session.request = lambda self, method, url, *a, **kw: request(
            method, url, *a, **kw
        )
    except ImportError:
        pass

    class Download(io.BytesIO):
        status = 200
        code = 200
        headers = {"Content-Type": "application/octet-stream"}

        def getcode(self):
            return 200

    def urlopen(url, *args, **kwargs):
        method = url.get_method() if isinstance(url, urllib.request.Request) else "GET"
        target = url.full_url if isinstance(url, urllib.request.Request) else url
        if (args and args[0] is not None) or kwargs.get("data") is not None:
            raise DownloadDenied("Download request bodies are not permitted")
        check(method, target)
        return Download(source_bytes)

    urllib.request.urlopen = urlopen

    def denied(*args, **kwargs):
        raise DownloadDenied("Network connections are disabled during validation")

    socket.create_connection = denied
    socket.socket.connect = denied
    socket.socket.connect_ex = denied


def main():
    input_dir, output_dir = Path(sys.argv[1]), Path(sys.argv[2])
    log = BoundedLog()
    try:
        request = json.loads((input_dir / "request.json").read_text(encoding="utf-8"))
        install_download_mock((input_dir / "source.bin").read_bytes(), request["source_id"])
        namespace = {"__name__": "candidate_parser", "__file__": str(input_dir / "candidate.py")}
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            code = (input_dir / "candidate.py").read_text(encoding="utf-8")
            exec(compile(code, "candidate.py", "exec"), namespace, namespace)
            entrypoint = request.get("entrypoint", "main")
            if entrypoint not in {"main", "_dc_validate"}:
                raise ValueError("Unsupported validation entrypoint")
            result = namespace[entrypoint](request["params"])
        if not isinstance(result, dict):
            raise TypeError("main(params) must return a dictionary")
        payload = {"ok": True, "result": result}
        # Strict encoding rejects NaN, infinity, datetimes and other non-JSON values.
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 8 * 1024 * 1024:
            raise ValueError("Execution result is too large")
    except BaseException as exc:
        payload = {
            "ok": False,
            "code": "download_denied" if isinstance(exc, DownloadDenied) else "execution_error",
            "message": f"{type(exc).__name__}: {str(exc)[:2000]}",
        }
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    (output_dir / "result.json").write_text(encoded, encoding="utf-8")
    (output_dir / "execution.log").write_text("".join(log.parts), encoding="utf-8")


if __name__ == "__main__":
    main()
