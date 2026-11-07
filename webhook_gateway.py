"""Token-protected webhook API and safe image ingestion helpers."""
import base64
import binascii
import http.client
import ipaddress
import json
import os
import re
import socket
import tempfile
import threading
from urllib.parse import unquote, urljoin, urlparse

import requests
import urllib3
from flask import Flask, jsonify, request
from waitress import create_server

MAX_IMAGE = 10 * 1024 * 1024
MAX_BODY = 15 * 1024 * 1024
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/bmp": ".bmp", "image/webp": ".webp"}


def validate_image(path):
    if os.path.getsize(path) > MAX_IMAGE: raise OverflowError("图片超过 10 MiB")
    with open(path, "rb") as f:
        head = f.read(16)
    if head.startswith(b"\x89PNG\r\n\x1a\n"): ext = ".png"
    elif head.startswith(b"\xff\xd8\xff"): ext = ".jpg"
    elif head.startswith((b"GIF87a", b"GIF89a")): ext = ".gif"
    elif head.startswith(b"BM"): ext = ".bmp"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP": ext = ".webp"
    else: raise ValueError("内容不是受支持的图片")
    return ext


def _public_url(url):
    if not isinstance(url, str): raise ValueError("图片 URL 必须是字符串")
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("图片 URL 必须是公网 HTTP/HTTPS 地址")
    port = p.port
    if port is None: port = 443 if p.scheme == "https" else 80
    if not 1 <= port <= 65535: raise ValueError("图片 URL 端口无效")
    try:
        addrs = [ipaddress.ip_address(x[4][0]) for x in socket.getaddrinfo(p.hostname, port, type=socket.SOCK_STREAM)]
    except OSError as exc: raise ValueError("图片 URL 域名无法解析") from exc
    if not addrs or any(not address.is_global for address in addrs):
        raise ValueError("图片 URL 不允许指向本机或内网地址")
    return addrs[0].compressed


def download_image(url, path):
    current = url
    for _ in range(4):
        address = _public_url(current)
        parsed = urlparse(current)
        port = parsed.port
        if port is None: port = 443 if parsed.scheme == "https" else 80
        target = parsed.path or "/"
        if parsed.query: target += "?" + parsed.query
        ip_host = f"[{address}]" if ":" in address else address
        pinned_url = f"{parsed.scheme}://{ip_host}:{port}{target}"
        manager = None
        try:
            proxy = requests.utils.select_proxy(current, requests.utils.get_environ_proxies(current))
            if proxy:
                proxy_parts = urlparse(proxy)
                proxy_headers = {}
                if proxy_parts.username is not None:
                    credentials = f"{unquote(proxy_parts.username)}:{unquote(proxy_parts.password or '')}"
                    proxy_headers = urllib3.util.make_headers(proxy_basic_auth=credentials)
                proxy_host = proxy_parts.hostname
                if not proxy_host: raise ValueError("图片下载代理地址无效")
                if ":" in proxy_host: proxy_host = f"[{proxy_host}]"
                if proxy_parts.port: proxy_host += f":{proxy_parts.port}"
                proxy_url = proxy_parts._replace(netloc=proxy_host).geturl()
                manager = urllib3.ProxyManager(proxy_url, proxy_headers=proxy_headers)
            else:
                manager = urllib3.PoolManager()

            hostname = parsed.hostname.encode("idna").decode("ascii")
            host_header = f"[{hostname}]" if ":" in hostname else hostname
            if parsed.port is not None: host_header += f":{parsed.port}"
            headers = {"Host": host_header}
            timeout = urllib3.Timeout(connect=5, read=15)
            if parsed.scheme == "https":
                pool = manager.connection_from_host(
                    address, port, scheme="https",
                    pool_kwargs={
                        "server_hostname": hostname,
                        "assert_hostname": hostname,
                        "ca_certs": requests.certs.where(),
                    },
                )
                response = pool.urlopen(
                    "GET", target, headers=headers, timeout=timeout, retries=False,
                    redirect=False, preload_content=False,
                )
            else:
                response = manager.urlopen(
                    "GET", pinned_url, headers=headers, timeout=timeout, retries=False,
                    redirect=False, preload_content=False,
                )

            with response as res:
                if res.status in (301, 302, 303, 307, 308):
                    location = res.headers.get("Location")
                    if not location: raise ValueError("图片 URL 跳转无效")
                    current = urljoin(current, location)
                    continue
                if res.status >= 400: raise requests.HTTPError(f"图片服务器返回 HTTP {res.status}")
                total = 0
                with open(path, "wb") as f:
                    for chunk in res.stream(65536, decode_content=True):
                        total += len(chunk)
                        if total > MAX_IMAGE: raise OverflowError("图片超过 10 MiB")
                        f.write(chunk)
        except urllib3.exceptions.HTTPError as exc:
            raise requests.RequestException("图片下载失败或超时") from exc
        finally:
            if manager: manager.clear()
        return validate_image(path)
    raise ValueError("图片 URL 跳转次数过多")


def decode_image(data, path):
    if not isinstance(data, str): raise ValueError("image_base64 必须是字符串")
    if data.startswith("data:"):
        m = re.fullmatch(r"data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=\r\n]+)", data)
        if not m or m.group(1).lower() not in MIME_EXT: raise ValueError("不支持的图片 Data URI")
        data = m.group(2)
    try: raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc: raise ValueError("Base64 编码无效") from exc
    if len(raw) > MAX_IMAGE: raise OverflowError("图片超过 10 MiB")
    with open(path, "wb") as f: f.write(raw)
    return validate_image(path)


def _request_management(port, path, method):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        payload = response.read()
        status = response.status
    except (OSError, http.client.HTTPException) as exc:
        raise ConnectionError("管理端不可用") from exc
    finally:
        connection.close()

    try:
        data = json.loads(payload.decode("utf-8")) if payload else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("管理端返回了无效 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("管理端返回的 JSON 必须是对象")
    return data, status


def create_app(token, call_bot, management_port):
    app = Flask("webhook_gateway")
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY

    def forward_management(path, method):
        try:
            data, status_code = _request_management(management_port, path, method)
        except ConnectionError:
            app.logger.warning("Management API request failed: %s %s", method, path)
            return jsonify(error="管理端不可用"), 503
        except ValueError:
            app.logger.exception("Management API returned an invalid response: %s %s", method, path)
            return jsonify(error="管理端响应格式无效"), 502
        return jsonify(data), status_code

    def bot_unavailable(exc):
        cause = exc.__cause__ or exc
        tb = cause.__traceback__
        while tb and tb.tb_next:
            tb = tb.tb_next
        origin = f"{os.path.basename(tb.tb_frame.f_code.co_filename)}:{tb.tb_lineno}" if tb else "unknown"
        app.logger.warning("Webhook bridge failed (%s): %s", type(cause).__name__, cause)
        return jsonify(error="bot 不可用", detail=f"{type(cause).__name__}: {cause} at {origin}"), 503

    @app.errorhandler(413)
    def too_large(_): return jsonify(error="请求体超过 15 MiB"), 413

    @app.before_request
    def authenticate():
        if request.headers.get("Authorization", "") != "Bearer " + token:
            return jsonify(error="Token 无效"), 401

    @app.errorhandler(Exception)
    def error(exc):
        if isinstance(exc, ValueError): return jsonify(error=str(exc)), 400
        app.logger.exception("Webhook API error")
        return jsonify(error="服务器内部错误"), 502

    @app.get("/api/v1/health")
    def health(): return jsonify(status="ok")
    @app.get("/api/v1/bot/status")
    def status(): return forward_management("/bot_status", "GET")
    @app.post("/api/v1/bot/start")
    def start(): return forward_management("/start_bot", "POST")
    @app.post("/api/v1/bot/stop")
    def stop(): return forward_management("/stop_bot", "POST")
    @app.post("/api/v1/messages")
    def message():
        body = request.get_json(silent=True)
        if not isinstance(body, dict): return jsonify(error="请求 JSON 必须是对象"), 400
        target, text = body.get("target"), body.get("text")
        if not isinstance(target, str) or not target.strip() or not isinstance(text, str) or not text: return jsonify(error="target 和 text 必填"), 400
        try: ok = call_bot("text", target, text)
        except ConnectionError as exc: return bot_unavailable(exc)
        return (jsonify(status="sent"), 200) if ok else (jsonify(error="微信发送失败"), 502)
    @app.post("/api/v1/images")
    def image():
        body = request.get_json(silent=True)
        if not isinstance(body, dict): return jsonify(error="请求 JSON 必须是对象"), 400
        target = body.get("target")
        keys = [k for k in ("image_url", "image_base64") if body.get(k)]
        if not isinstance(target, str) or not target.strip() or len(keys) != 1: return jsonify(error="target 必填，且 image_url/image_base64 必须且只能提供一个"), 400
        fd, path = tempfile.mkstemp(prefix="wechatbot-webhook-"); os.close(fd)
        try:
            try: ext = download_image(body["image_url"], path) if keys[0] == "image_url" else decode_image(body["image_base64"], path)
            except OverflowError as exc: return jsonify(error=str(exc)), 413
            except requests.RequestException as exc: return jsonify(error="图片下载失败或超时"), 400
            try: ok = call_bot("image", target, path)
            except ConnectionError as exc: return bot_unavailable(exc)
            return (jsonify(status="sent", format=ext[1:]), 200) if ok else (jsonify(error="微信发送失败"), 502)
        except ValueError as exc: return jsonify(error=str(exc)), 415 if str(exc).startswith("内容不是") else 400
        finally:
            try: os.unlink(path)
            except OSError: pass
    return app


class Gateway:
    def __init__(self, app, host, port): self.server = create_server(app, host=host, port=port, threads=8)
    def start(self):
        self.thread = threading.Thread(target=self.server.run, name="WebhookGateway", daemon=True)
        self.thread.start()
    def stop(self): self.server.close()
