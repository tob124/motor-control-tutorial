"""纯标准库的本地 HTTP 服务。

课程作者：Connor He 和 Astra。

为什么不用框架：本机 Python 3.12 **没有 pip、没有 aiohttp**（已实测），
而课程要在任何一台 Ubuntu 上"解压即用"。所以这里只用
``http.server`` + ``socketserver`` + ``threading``。

安全边界（与姊妹项目一致的思路，但只监听本机、只服务本机浏览器）：

* 只绑定 ``127.0.0.1``；
* 校验 ``Host``、``Origin``、``Sec-Fetch-Site``，拒绝跨站请求；
* 除 ``/api/session`` 外所有 ``/api/*`` 都需要一次性令牌
  ``X-Course-Token``（WebSocket 用子协议头携带）；
* 静态文件只从 ``dist/`` 取，路径穿越一律拒绝；
* 统一注入 CSP / nosniff / no-store 等响应头。
"""
from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

__all__ = ['Router', 'CourseServer', 'make_handler', 'serve']

MAX_BODY = 256 * 1024

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; font-src 'self'; "
       "connect-src 'self' ws://127.0.0.1:* ws://localhost:*; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

SAFE_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'X-Frame-Options': 'DENY',
    'Content-Security-Policy': CSP,
}


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Router:
    """极简路由表：``(method, path_pattern)`` → 处理函数。

    路径参数支持 ``{name}`` 形式，例如 ``/api/sim/runs/{id}``。
    """

    def __init__(self):
        self.routes = []

    def add(self, method, pattern, handler):
        parts = [seg for seg in pattern.strip('/').split('/') if seg != '']
        self.routes.append((method.upper(), parts, handler))

    def get(self, pattern, handler):
        self.add('GET', pattern, handler)

    def post(self, pattern, handler):
        self.add('POST', pattern, handler)

    def resolve(self, method, path):
        parts = [seg for seg in path.strip('/').split('/') if seg != '']
        allowed = set()
        for route_method, pattern_parts, handler in self.routes:
            if len(pattern_parts) != len(parts):
                continue
            params = {}
            matched = True
            for expected, actual in zip(pattern_parts, parts):
                if expected.startswith('{') and expected.endswith('}'):
                    params[expected[1:-1]] = urllib.parse.unquote(actual)
                elif expected != actual:
                    matched = False
                    break
            if not matched:
                continue
            if route_method == method.upper():
                return handler, params
            allowed.add(route_method)
        if allowed:
            raise HttpError(405, f'该方法不被支持，允许：{"、".join(sorted(allowed))}。')
        return None, None


class CourseServer:
    """把路由、静态文件和令牌串起来。"""

    def __init__(self, root, token, state_dir, host='127.0.0.1', port=8770):
        self.root = Path(root)
        self.dist = self.root / 'dist'
        self.token = token
        self.state_dir = Path(state_dir)
        self.host = host
        self.port = port
        self.router = Router()
        self.hosts = {f'{host}:{port}', f'localhost:{port}', f'127.0.0.1:{port}'}
        self.origins = {f'http://{name}' for name in self.hosts} | {''}
        self.started = time.time()

    # ------------------------------------------------------------ 校验
    def check_request(self, method, path, headers, client_host, query=None):
        host = headers.get('Host', '')
        if host not in self.hosts:
            raise HttpError(403, '只允许从本机课程页面访问。')
        origin = headers.get('Origin', '')
        if origin not in self.origins:
            raise HttpError(403, '请求来源不被信任。')
        if headers.get('Sec-Fetch-Site', '') == 'cross-site':
            raise HttpError(403, '拒绝跨站请求。')
        if path.startswith('/api/') and path != '/api/session':
            supplied = headers.get('X-Course-Token', '')
            if not supplied and query:
                # 下载类接口可以用查询串带令牌：浏览器直接打开下载链接时
                # 无法自定义请求头，这是唯一可行的方式，且令牌本来就是一次性的。
                supplied = (query.get('token') or [''])[0]
            if not secrets_equal(supplied, self.token):
                raise HttpError(401, '页面会话已过期，请刷新页面。')

    def static_path(self, url_path):
        """把 URL 映射到 ``dist/`` 下的真实文件，拒绝任何穿越尝试。"""
        clean = urllib.parse.unquote(url_path)
        if '\x00' in clean:
            raise HttpError(400, '路径不合法。')
        clean = posixpath.normpath(clean)
        if clean in ('/', ''):
            clean = '/index.html'
        if clean.startswith('/..') or '..' in clean.split('/'):
            raise HttpError(403, '不允许访问该路径。')
        candidate = (self.dist / clean.lstrip('/')).resolve()
        try:
            candidate.relative_to(self.dist.resolve())
        except ValueError:
            raise HttpError(403, '不允许访问该路径。')
        return candidate


def secrets_equal(a, b):
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a.encode('utf-8'), b.encode('utf-8')):
        diff |= x ^ y
    return diff == 0


class Handler(BaseHTTPRequestHandler):
    server_version = 'RoboconCourse/1.0'
    protocol_version = 'HTTP/1.1'

    # ------------------------------------------------------------ 基础
    def log_message(self, fmt, *args):
        # 保持安静：这是本机学习服务，不需要把每个请求都打到终端
        if os.environ.get('COURSE_VERBOSE'):
            super().log_message(fmt, *args)

    @property
    def course(self):
        return self.server.course

    def _send(self, status, body, content_type='application/json; charset=utf-8', extra=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        for key, value in SAFE_HEADERS.items():
            self.send_header(key, value)
        if self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'no-store')
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, status, value):
        try:
            text = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            status, text = 500, json.dumps(
                {'error': '响应里出现了非法数值，已中止。请检查仿真参数。'}, ensure_ascii=False)
        self._send(status, text)

    def _read_json(self):
        length = self.headers.get('Content-Length')
        if not length:
            return {}
        try:
            size = int(length)
        except ValueError:
            raise HttpError(400, '请求长度不合法。')
        if size < 0 or size > MAX_BODY:
            raise HttpError(413, '请求体过大。')
        raw = self.rfile.read(size)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise HttpError(400, '请求不是合法的 JSON。')

    # ------------------------------------------------------------ 动词
    def do_GET(self):
        self._dispatch('GET')

    def do_HEAD(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def _dispatch(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        try:
            query = urllib.parse.parse_qs(parsed.query)
            self.course.check_request(method, path, self.headers, self.client_address[0], query)
            if path.startswith('/api/'):
                handler, params = self.course.router.resolve(method, path)
                if handler is None:
                    raise HttpError(404, '没有这个接口。')
                body = self._read_json() if method == 'POST' else {}
                result = handler(self, params, query, body)
                if result is None:
                    return                      # 处理函数已经自己写过响应（WebSocket 等）
                status, payload = result if isinstance(result, tuple) else (200, result)
                self._json(status, payload)
            else:
                self._serve_static(path)
        except HttpError as error:
            self._json(error.status, {'error': error.message})
        except BrokenPipeError:
            pass
        except (ValueError, KeyError, TypeError) as error:
            # 处理函数用这些异常表示"请求参数不对"（与姊妹项目的约定一致），
            # 所以映射成 400 而不是 500：这是学员在页面上会看到的提示。
            self._json(400, {'error': str(error)[:400] or '请求格式不正确。'})
        except Exception as error:                       # noqa: BLE001
            # 兜底：任何没预料到的异常都转成 500，并把类型带出来便于排查
            self._json(500, {'error': f'服务内部错误：{type(error).__name__}: {error}'[:400]})

    def _serve_static(self, path):
        target = self.course.static_path(path)
        if not target.is_file():
            raise HttpError(404, '找不到页面资源。')
        ctype, _ = mimetypes.guess_type(str(target))
        if ctype is None:
            ctype = 'application/octet-stream'
        if ctype.startswith('text/') or ctype in ('application/javascript', 'application/json'):
            ctype += '; charset=utf-8'
        data = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        for key, value in SAFE_HEADERS.items():
            self.send_header(key, value)
        if target.name == 'index.html':
            self.send_header('Cache-Control', 'no-store')
        else:
            self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)


def make_server(course, host, port):
    handler = type('BoundHandler', (Handler,), {})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    httpd.course = course
    return httpd


def serve(course, host='127.0.0.1', port=8770):
    httpd = make_server(course, host, port)
    thread = threading.Thread(target=httpd.serve_forever, name='http', daemon=True)
    thread.start()
    return httpd, thread
