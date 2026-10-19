import os
import json
import time
import http.server
import socketserver
import math
import ipaddress
from api_limits import RequestLimits, LimitExceeded
from urllib import error, request
from urllib.parse import unquote, urlparse

PORT = int(os.environ.get("PORT", 8080))
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DASHSCOPE_CREATE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
DASHSCOPE_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
PUBLIC_ASSET_PREFIX = "assets/"
PUBLIC_ASSET_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".ico", ".mp3", ".wav", ".ogg"}
BLOCKED_FILE_NAMES = {
    "server.py",
    "Dockerfile",
    "render.yaml",
    "requirements.txt",
    "README.md",
    ".dockerignore",
    ".env",
    ".env.example",
}
BLOCKED_PATH_PARTS = {"..", ".git", "__pycache__", ".venv", "venv", "node_modules"}
BLOCKED_EXTENSIONS = {
    ".py",
    ".pyc",
    ".env",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".md",
    ".txt",
    ".log",
    ".zip",
    ".tar",
    ".gz",
    ".rar",
    ".7z",
}

Handler = http.server.SimpleHTTPRequestHandler

class BadRequest(Exception):
    pass


def validate_chat(payload):
    if not isinstance(payload, dict):
        raise BadRequest('请求格式不正确。')
    if payload.get('model', 'deepseek-chat') != 'deepseek-chat':
        raise BadRequest('不支持该模型。')
    if set(payload) - {'model', 'messages', 'temperature', 'max_tokens'}:
        raise BadRequest('请求包含不支持的参数。')
    messages = payload.get('messages')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
        raise BadRequest('对话长度超出限制。')
    clean, total = [], 0
    for message in messages:
        if not isinstance(message, dict) or message.get('role') not in {'system', 'user', 'assistant'}:
            raise BadRequest('对话格式不正确。')
        content = message.get('content')
        if not isinstance(content, str) or not content.strip() or len(content) > 8000:
            raise BadRequest('单条对话过长或为空。')
        total += len(content)
        clean.append({'role': message['role'], 'content': content})
    if total > 24000:
        raise BadRequest('对话内容过长，请开启新的冒险。')
    tokens, temperature = payload.get('max_tokens', 1500), payload.get('temperature', .85)
    if type(tokens) is not int or not 1 <= tokens <= 1500:
        raise BadRequest('回复长度超出限制。')
    if type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 1.5:
        raise BadRequest('生成参数不正确。')
    return {'model': 'deepseek-chat', 'messages': clean, 'max_tokens': tokens, 'temperature': temperature}


class MyHandler(Handler):
    limits = None

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_not_found(self, head_only=False):
        body = b"Not Found"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _is_public_asset(self, requested_path):
        if not requested_path.startswith(PUBLIC_ASSET_PREFIX):
            return False
        _, ext = os.path.splitext(requested_path.lower())
        return ext in PUBLIC_ASSET_EXTENSIONS

    def _is_blocked_path(self, requested_path):
        normalized = requested_path.strip("/")
        if not normalized:
            return False
        parts = [part for part in normalized.split("/") if part]
        if any(part in BLOCKED_PATH_PARTS or part.startswith(".") for part in parts):
            return True
        filename = parts[-1]
        if filename in BLOCKED_FILE_NAMES:
            return True
        _, ext = os.path.splitext(filename.lower())
        return ext in BLOCKED_EXTENSIONS

    def _route_static_request(self, head_only=False):
        requested_path = unquote(urlparse(self.path).path).lstrip("/")
        if requested_path and self._is_blocked_path(requested_path):
            return self._send_not_found(head_only=head_only)
        if requested_path and os.path.isfile(requested_path):
            if not self._is_public_asset(requested_path):
                return self._send_not_found(head_only=head_only)
            if head_only:
                return Handler.do_HEAD(self)
            return Handler.do_GET(self)

        # 非静态资源路径都返回 baiyang.html，方便 Render 直接打开根路径
        self.path = "/baiyang.html"
        if head_only:
            return Handler.do_HEAD(self)
        return Handler.do_GET(self)

    def _read_json(self):
        if self.headers.get('Transfer-Encoding'):
            raise BadRequest('不支持该请求格式。')
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            raise BadRequest('请使用 JSON 请求。')
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise BadRequest('请求长度不正确。')
        if not 0 < length <= 65536:
            raise BadRequest('请求内容过大或为空。')
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise BadRequest('请求未完整发送，请重试。')
        try:
            value = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BadRequest('请求格式不正确。')
        if not isinstance(value, dict):
            raise BadRequest('请求格式不正确。')
        return value

    def _post_json(self, url, payload, headers, timeout=60):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method="POST")
        return self._open_json(req, timeout)

    def _get_json(self, url, headers, timeout=30):
        req = request.Request(url, headers=headers, method="GET")
        return self._open_json(req, timeout)

    def _open_json(self, req, timeout):
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return resp.status, json.loads(body) if body else {}
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {"message": body}
            return exc.code, payload

    def do_GET(self):
        print(f"请求路径: {self.path}")
        if self.path == "/healthz":
            self._send_json(200, {"ok": True})
            return
        if self.path.startswith("/api/"):
            self._send_json(404, {"message": "接口不存在"})
            return
        return self._route_static_request()

    def do_HEAD(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", "12")
            self.end_headers()
            return
        if self.path.startswith("/api/"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        return self._route_static_request(head_only=True)

    def client_identity(self):
        peer = self.client_address[0]
        trusted = {item.strip() for item in os.environ.get('TRUSTED_PROXY_IPS', '').split(',') if item.strip()}
        if peer not in trusted:
            return peer
        forwarded = self.headers.get('X-Forwarded-For', '')
        if not forwarded or len(forwarded) > 512:
            return peer
        try:
            chain = [str(ipaddress.ip_address(part.strip())) for part in forwarded.split(',')]
        except ValueError:
            return peer
        # Walk from the known proxy towards the client; ignore spoofed prefixes.
        for address in reversed(chain):
            if address not in trusted:
                return address
        return peer

    def do_POST(self):
        kind = {'/api/chat': 'chat', '/api/image': 'image'}.get(self.path)
        if kind is None:
            self._send_json(404, {'message': '接口不存在'})
            return
        # Forwarded addresses are accepted only from explicitly configured proxies.
        client = self.client_identity()
        acquired = False
        try:
            payload = self._read_json()
            if kind == 'chat':
                payload = validate_chat(payload)
            else:
                if set(payload) - {'scene'} or not isinstance(payload.get('scene'), str) or not 1 <= len(payload['scene'].strip()) <= 800:
                    raise BadRequest('场景描述需为 1—800 字。')
            self.limits.acquire(kind, client)
            acquired = True
            if kind == 'chat':
                self.handle_chat(payload)
            else:
                self.handle_image(payload)
        except BadRequest as exc:
            self._send_json(400, {'message': str(exc)})
        except LimitExceeded as exc:
            body = json.dumps({'message': str(exc), 'retry_after': exc.retry_after}, ensure_ascii=False).encode('utf-8')
            self.send_response(429)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Retry-After', str(exc.retry_after))
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._send_json(503, {'message': '服务暂时不可用，请稍后重试。'})
        finally:
            if acquired:
                self.limits.release(kind, client)

    def handle_chat(self, payload):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            self._send_json(500, {"message": "剧情服务暂未就绪，请稍后重试。"})
            return

        try:
            status, data = self._post_json(
                DEEPSEEK_URL,
                payload,
                {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            if status >= 400:
                self._send_json(502, {'message': '剧情服务暂时不可用，请重试本次行动。'})
            else:
                self._send_json(status, data)
        except Exception:
            self._send_json(502, {'message': '剧情连接超时或中断，请重试本次行动。'})

    def handle_image(self, payload):
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            self._send_json(500, {"message": "场景图片暂时不可用，可继续游玩。"})
            return

        try:
            scene = str(payload.get("scene") or "").strip()
            prompt = "中国传媒大学校园场景，暖色调，治愈系插画风格，柔光，" + (
                scene or "白杨大陆校园场景，柔光，温暖"
            )
            create_payload = {
                "model": os.environ.get("DASHSCOPE_IMAGE_MODEL", "wanx2.0-t2i-turbo"),
                "input": {"prompt": prompt[:800]},
                "parameters": {
                    "size": os.environ.get("DASHSCOPE_IMAGE_SIZE", "1024*1024"),
                    "n": 1,
                    "prompt_extend": True,
                    "watermark": False,
                },
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",
            }
            deadline = time.monotonic() + 80
            status, data = self._post_json(DASHSCOPE_CREATE_URL, create_payload, headers, timeout=20)
            if status >= 400:
                self._send_json(502, {"message": "场景图片暂时无法生成，可继续游玩。"})
                return

            results = data.get("output", {}).get("results") or []
            if results and results[0].get("url"):
                self._send_json(200, {"url": results[0]["url"]})
                return

            task_id = data.get("output", {}).get("task_id")
            if not task_id:
                self._send_json(502, {"message": "场景图片暂时无法生成，可继续游玩。"})
                return

            poll_interval = int(os.environ.get("DASHSCOPE_POLL_INTERVAL", "10"))
            max_attempts = int(os.environ.get("DASHSCOPE_MAX_POLLS", "12"))
            poll_headers = {"Authorization": f"Bearer {api_key}"}
            for _ in range(max_attempts):
                remaining = deadline - time.monotonic()
                if remaining <= 1:
                    break
                time.sleep(min(max(1, poll_interval), max(0, remaining - 1)))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                poll_status, poll_data = self._get_json(
                    DASHSCOPE_TASK_URL.format(task_id=task_id),
                    poll_headers,
                    timeout=min(10, remaining),
                )
                if poll_status >= 400:
                    self._send_json(502, {"message": "场景图片暂时无法生成，可继续游玩。"})
                    return
                output = poll_data.get("output", {})
                task_status = output.get("task_status")
                if task_status == "SUCCEEDED":
                    results = output.get("results") or []
                    if results and results[0].get("url"):
                        self._send_json(200, {"url": results[0]["url"], "task_id": task_id})
                        return
                    self._send_json(502, {"message": "场景图片暂时无法生成，可继续游玩。"})
                    return
                if task_status in {"FAILED", "CANCELED", "UNKNOWN"}:
                    self._send_json(502, {"message": "场景图片暂时无法生成，可继续游玩。"})
                    return

            self._send_json(504, {"message": "场景图片生成超时，可继续游玩。"})
        except Exception:
            self._send_json(502, {"message": "场景图片暂时无法生成，可继续游玩。"})

class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

def main():
    MyHandler.limits = RequestLimits()
    with ReusableTCPServer(("0.0.0.0", PORT), MyHandler) as httpd:
        print(f"白杨大陆 服务器启动 - 端口 {PORT}")
        httpd.serve_forever()


if __name__ == '__main__':
    main()
