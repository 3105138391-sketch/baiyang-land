import os
import json
import time
import http.server
import socketserver
from urllib import error, request
from urllib.parse import unquote, urlparse

PORT = int(os.environ.get("PORT", 8080))
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DASHSCOPE_CREATE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
DASHSCOPE_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

Handler = http.server.SimpleHTTPRequestHandler

class MyHandler(Handler):
    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

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
        requested_path = unquote(urlparse(self.path).path).lstrip("/")
        if requested_path and os.path.isfile(requested_path):
            return Handler.do_GET(self)

        # 非静态资源路径都返回 baiyang.html，方便 Render 直接打开根路径
        self.path = "/baiyang.html"
        return Handler.do_GET(self)

    def do_POST(self):
        if self.path == "/api/chat":
            return self.handle_chat()
        if self.path == "/api/image":
            return self.handle_image()
        self._send_json(404, {"message": "接口不存在"})

    def handle_chat(self):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            self._send_json(500, {"message": "缺少环境变量 DEEPSEEK_API_KEY"})
            return

        try:
            payload = self._read_json()
            status, data = self._post_json(
                DEEPSEEK_URL,
                payload,
                {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            self._send_json(status, data)
        except Exception as exc:
            self._send_json(500, {"message": f"对话接口调用失败: {exc}"})

    def handle_image(self):
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            self._send_json(500, {"message": "缺少环境变量 DASHSCOPE_API_KEY"})
            return

        try:
            payload = self._read_json()
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
            status, data = self._post_json(DASHSCOPE_CREATE_URL, create_payload, headers)
            if status >= 400:
                self._send_json(status, data)
                return

            results = data.get("output", {}).get("results") or []
            if results and results[0].get("url"):
                self._send_json(200, {"url": results[0]["url"]})
                return

            task_id = data.get("output", {}).get("task_id")
            if not task_id:
                self._send_json(502, {"message": "DashScope 未返回 task_id", "detail": data})
                return

            poll_interval = int(os.environ.get("DASHSCOPE_POLL_INTERVAL", "10"))
            max_attempts = int(os.environ.get("DASHSCOPE_MAX_POLLS", "12"))
            poll_headers = {"Authorization": f"Bearer {api_key}"}
            for _ in range(max_attempts):
                time.sleep(poll_interval)
                poll_status, poll_data = self._get_json(
                    DASHSCOPE_TASK_URL.format(task_id=task_id),
                    poll_headers,
                )
                if poll_status >= 400:
                    self._send_json(poll_status, poll_data)
                    return
                output = poll_data.get("output", {})
                task_status = output.get("task_status")
                if task_status == "SUCCEEDED":
                    results = output.get("results") or []
                    if results and results[0].get("url"):
                        self._send_json(200, {"url": results[0]["url"], "task_id": task_id})
                        return
                    self._send_json(502, {"message": "DashScope 任务成功但未返回图片 URL", "detail": poll_data})
                    return
                if task_status in {"FAILED", "CANCELED", "UNKNOWN"}:
                    self._send_json(502, {"message": f"DashScope 生图任务失败: {task_status}", "detail": poll_data})
                    return

            self._send_json(504, {"message": "DashScope 生图超时", "task_id": task_id})
        except Exception as exc:
            self._send_json(500, {"message": f"生图接口调用失败: {exc}"})

class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

with ReusableTCPServer(("0.0.0.0", PORT), MyHandler) as httpd:
    print(f"白杨大陆 服务器启动 - 端口 {PORT}")
    httpd.serve_forever()
