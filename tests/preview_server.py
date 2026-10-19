"""Local browser acceptance fixture. No external API calls; not a production entrypoint."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api_limits import RequestLimits
from server import MyHandler, ReusableTCPServer

class PreviewHandler(MyHandler):
    calls = 0
    def handle_image(self, payload):
        self._send_json(200, {'url': None})
    def handle_chat(self, payload):
        PreviewHandler.calls += 1
        if PreviewHandler.calls == 2:
            self._send_json(502, {'message':'测试连接中断，请重试本次行动。'})
            return
        text = ('测试剧情：算法教务核心出现。\n⚔ 道韵净化战 | 算法教务核心 | 异化值80/80 | 护盾30 | ❤️100 💎100 ⚡3 ✨0' if PreviewHandler.calls == 1 else
                '测试剧情：本次净化已完成。\n📍中传北门 | ❤️100 💎90 ⚡2 ✨0\nA. 继续探索\nB. 观察校园\nC. 与灵兽交谈\nD. 查看线索')
        self._send_json(200, {'choices':[{'message':{'content':text}}]})

if __name__ == '__main__':
    os.chdir(Path(__file__).resolve().parents[1])
    with tempfile.TemporaryDirectory() as temp:
        PreviewHandler.limits = RequestLimits(temp + '/limits.sqlite')
        with ReusableTCPServer(('127.0.0.1', 18081), PreviewHandler) as server:
            print('Local fixture only: http://127.0.0.1:18081', flush=True)
            server.serve_forever()
