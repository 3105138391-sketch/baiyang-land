import http.server
import socketserver
import os

PORT = int(os.environ.get("PORT", 8080))

Handler = http.server.SimpleHTTPRequestHandler

class MyHandler(Handler):
    def do_GET(self):
        print(f"请求路径: {self.path}")
        # 所有路径都返回 baiyang.html
        self.path = "/baiyang.html"
        return Handler.do_GET(self)

with socketserver.TCPServer(("0.0.0.0", PORT), MyHandler) as httpd:
    print(f"白杨大陆 服务器启动 - 端口 {PORT}")
    httpd.serve_forever()
