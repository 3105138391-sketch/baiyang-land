import http.server, socketserver, os
PORT = int(os.environ.get("PORT", 8080))
class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.path = "/baiyang.html"
        return super().do_GET()
with socketserver.TCPServer(("0.0.0.0", PORT), H) as httpd:
    print(f"Serving on port {PORT}")
    httpd.serve_forever()
