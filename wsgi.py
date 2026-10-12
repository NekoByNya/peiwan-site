# -*- coding: utf-8 -*-
"""
================================================================================
 WSGI 适配层（给 PythonAnywhere 这类"只接受 WSGI"的平台用）
================================================================================
 它做的事：把 WSGI 的请求 → 转成 app.py 能理解的形式 → 拿回响应 → 再交回 WSGI。

 为什么要这样：app.py 原本是一个"自己监听端口"的完整服务器（在 Render、在
 你自己电脑上都是这么跑的）。PythonAnywhere 免费版不允许自己监听端口，只允许
 提供 WSGI 接口。所以这里加一层薄薄的转换，app.py 一行都不用改。

 本地测试：
     python wsgi.py            # 然后浏览器打开 http://127.0.0.1:8124
 部署到 PythonAnywhere：
     见 DEPLOY-PYTHONANYWHERE.md
================================================================================
"""

import io
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from urllib.parse import quote  # noqa: E402

import app as site                     # noqa: E402  这就是你的 app.py


class _Capture:
    """假装的"socket 写口"：把服务器写出去的响应字节收集起来（关闭时不清空）"""

    def __init__(self):
        self._chunks = []

    def write(self, data):
        if data:
            self._chunks.append(bytes(data))
        return len(data or b"")

    def flush(self):
        pass

    def close(self):
        pass                                     # 故意什么都不做，关闭后还要读内容

    @property
    def closed(self):
        return False

    def writable(self):
        return True

    def getvalue(self):
        return b"".join(self._chunks)


class _FakeSocket:
    """假装的 socket：让 app.py 里那个 BaseHTTPRequestHandler 能正常工作"""

    def __init__(self, request_bytes):
        self._rfile = io.BytesIO(request_bytes)
        self._wfile = _Capture()

    def makefile(self, mode, *args, **kwargs):
        return self._rfile if "r" in mode else self._wfile

    # Python 3.12+ 的 http.server 在写响应时直接调 socket.sendall()，
    # 所以这个假 socket 必须提供它（少了它就会报 AttributeError）
    def sendall(self, data):
        self._wfile.write(bytes(data))

    def send(self, data):
        self._wfile.write(bytes(data))
        return len(data)

    def settimeout(self, *a):
        pass

    def getsockname(self):
        return ("127.0.0.1", 0)

    def getpeername(self):
        return ("127.0.0.1", 0)

    def close(self):
        pass

    def shutdown(self, *a):
        pass


def _build_raw_request(environ, body):
    """把 WSGI 的 environ 拼回一条原始 HTTP 请求"""
    method = environ.get("REQUEST_METHOD") or "GET"
    # PATH_INFO 已经被 WSGI 解码过，这里重新编码，保证 app.py 里的解码逻辑行为一致
    path = quote(environ.get("PATH_INFO") or "/", safe="/%")
    query = environ.get("QUERY_STRING") or ""
    target = path + (("?" + query) if query else "")

    lines = ["%s %s HTTP/1.0" % (method, target)]
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            header = key[5:].replace("_", "-").title()
            if header.lower() in ("content-length", "content-type"):
                continue
            lines.append("%s: %s" % (header, value))
    host = environ.get("HTTP_HOST") or environ.get("SERVER_NAME") or "localhost"
    lines.append("Host: %s" % host)
    lines.append("Content-Type: %s" % (environ.get("CONTENT_TYPE") or "application/json"))
    lines.append("Content-Length: %d" % len(body))
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8", "replace") + body


def _parse_raw_response(raw):
    """把 app.py 写出的原始 HTTP 响应拆成 (状态行, 头列表, 正文)"""
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = lines[0].split(" ", 1)[1] if " " in lines[0] else "200 OK"
    headers = []
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers.append((k.strip(), v.strip()))
    return status, headers, body


def application(environ, start_response):
    """WSGI 入口：PythonAnywhere 会调用这个函数"""
    length = 0
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    body = environ["wsgi.input"].read(length) if length > 0 else b""

    raw_request = _build_raw_request(environ, body)
    sock = _FakeSocket(raw_request)

    # 直接把请求交给 app.py 里那个处理器——它以为自己在一个真的 socket 上工作
    site.Handler(sock, ("127.0.0.1", 0), None)

    raw_response = sock._wfile.getvalue()
    status, headers, resp_body = _parse_raw_response(raw_response)

    # WSGI 不允许传"逐跳"头，去掉它们
    drop = {"connection", "keep-alive", "transfer-encoding", "upgrade", "proxy-authenticate",
            "proxy-authorization", "te", "trailer"}
    headers = [(k, v) for k, v in headers if k.lower() not in drop]

    start_response(status, headers)
    # HEAD 请求：app.py 已经保证没有正文，这里原样返回即可
    return [resp_body]


def _main():
    """本地测试用的小服务器（PythonAnywhere 上不会跑这段）"""
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIServer, make_server

    class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    port = int(os.environ.get("PORT") or 8124)
    site.init_db()
    with make_server("127.0.0.1", port, application, server_class=ThreadingWSGIServer) as httpd:
        print("WSGI 模式已启动：http://127.0.0.1:%d" % port)
        print("数据目录：%s" % site.DATA_DIR)
        httpd.serve_forever()


if __name__ == "__main__":
    _main()
