"""An in-process fixture for the installed home's GitHub reader on a disposable CI machine.

Only the test service uses this proxy. It never connects upstream or stores headers.
It serves immutable ZIP files (or JSON) selected before the process starts.
"""

import argparse
import functools
import http.server
import subprocess
import shutil
import ssl
from pathlib import Path
from urllib.parse import urlsplit


class Feed(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, directory, tls, **kwargs):
        self.directory = directory
        self.tls = tls
        super().__init__(*args, **kwargs)

    def do_CONNECT(self):
        if self.path != "api.github.com:443":
            self.send_error(403)
            return
        self.send_response_only(200)
        self.end_headers()
        self.wfile.flush()
        self.request = self.tls.wrap_socket(self.connection, server_side=True)
        self.setup()
        self.close_connection = False

    def do_GET(self):
        prefix = "/repos/qualification/panels/"
        path = urlsplit(self.path).path
        if (not isinstance(self.connection, ssl.SSLSocket)
                or self.headers.get("Authorization") != "Bearer local-qualification"
                or not path.startswith(prefix)):
            self.send_error(403)
            return
        asset = path.removeprefix(prefix)
        name = {"releases": "releases.json", "releases/assets/1": "publication.json",
                "releases/assets/2": "app.zip", "releases/assets/3": "machine.zip",
                "releases/assets/4": "mac-home.zip"}.get(asset)
        if not name or not (self.directory / name).is_file():
            self.send_error(404)
            return
        file = self.directory / name
        self.send_response(200)
        self.send_header("Content-Length", str(file.stat().st_size))
        self.send_header("Content-Type", "application/json" if name.endswith(".json") else "application/zip")
        self.end_headers()
        with file.open("rb") as stream:
            shutil.copyfileobj(stream, self.wfile)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    # -config works with the runner's OpenSSL or LibreSSL without relying on -addext.
    config = args.directory / "openssl.cnf"
    config.write_text("[req]\nprompt=no\ndistinguished_name=subject\nx509_extensions=extensions\n"
                      "[subject]\nCN=api.github.com\n[extensions]\nsubjectAltName=DNS:api.github.com\n")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-config", str(config), "-keyout", str(args.directory / "key.pem"),
                    "-out", str(args.directory / "cert.pem")], check=True, stderr=subprocess.DEVNULL)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(args.directory / "cert.pem", args.directory / "key.pem")
    handler = functools.partial(Feed, directory=args.directory, tls=tls)
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        print(server.server_port, flush=True)
        server.serve_forever()
