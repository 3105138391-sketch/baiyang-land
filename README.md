# baiyang-land

## 本地运行

先在 DeepSeek 和阿里云百炼控制台重新生成 API Key，并配置为环境变量：

```bash
export DEEPSEEK_API_KEY="你的 DeepSeek API Key"
export DASHSCOPE_API_KEY="你的阿里云百炼 DashScope API Key"
python server.py
```

打开 `http://localhost:8080`。

可选配置：

```bash
export DASHSCOPE_IMAGE_MODEL="wanx2.0-t2i-turbo"
export DASHSCOPE_IMAGE_SIZE="1024*1024"
export DASHSCOPE_POLL_INTERVAL="10"
export DASHSCOPE_MAX_POLLS="12"
```

不要把 API Key 写进 `baiyang.html` 或提交到 GitHub。

## 在线部署

这个项目需要后端代理调用 DeepSeek 和 DashScope，所以不要部署到 GitHub Pages 这类纯静态托管平台。推荐部署到支持 Docker Web Service 的平台，例如 Render、Railway、Koyeb、Fly.io。

### Render 部署

1. 先撤销已经提交到旧版 `baiyang.html` 里的 API Key，并重新生成新 Key。
2. 把当前代码推送到 GitHub。
3. 在 Render 创建 Web Service，选择这个 GitHub 仓库。
4. Runtime 选择 Docker，或者使用仓库里的 `render.yaml` 蓝图。
5. 在 Render 的 Environment 里配置：

```bash
DEEPSEEK_API_KEY=新的 DeepSeek API Key
DASHSCOPE_API_KEY=新的 DashScope API Key
DASHSCOPE_IMAGE_MODEL=wanx2.0-t2i-turbo
DASHSCOPE_IMAGE_SIZE=1024*1024
DASHSCOPE_POLL_INTERVAL=10
DASHSCOPE_MAX_POLLS=12
```

部署完成后，Render 会提供一个公网 URL。网页会同源请求 `/api/chat` 和 `/api/image`，API Key 只存在服务端环境变量里。

### Docker 本地验证

```bash
docker build -t baiyang-land .
docker run --rm -p 8080:8080 \
  -e DEEPSEEK_API_KEY="你的 DeepSeek API Key" \
  -e DASHSCOPE_API_KEY="你的 DashScope API Key" \
  baiyang-land
```


## 第一轮可靠性更新

- 战斗请求失败后保留行动，提供重试和返回选项；成功后只提交一次历史与进度。
- 当前浏览器自动保存完整剧情与进度，刷新后可继续；清除网站数据、隐私模式或换设备不会保留存档。重开需确认。
- 剧情直接生成，场景变化、进入战斗或关键事件才生成图片；图片失败保留校园实景。
- 对话/图片分别有60/90秒前端超时；服务端图片轮询总预算80秒。正文仍采用原有打字效果，可点“立即显示全文”。
- 服务端固定 deepseek-chat，最多1500输出token，单次请求64KiB。每个直连IP每分钟12次剧情、4次图片；全站每天600次剧情、120次图片。次数从请求被接受时计入，失败也计数。每日按UTC零点（北京时间08:00）重置。
- 每IP最多2个进行中请求；全站同时8个剧情/3个图片。超过限制返回429和Retry-After。可通过环境变量调整，参见 `.env.example`。

调用次数上限用来限制成本，不是精确人民币预算。未添加登录系统；若前置代理，默认不信任X-Forwarded-For。核实反代地址后设置TRUSTED_PROXY_IPS为反代的精确IP（逗号分隔），只信任这些来源追加的转发链；代理必须正确追加/覆盖该头，且避免直接访问源站绕过代理限制。SQLite计数必须保存在持久卷；单实例部署，不支持多个独立容器共享各自计数的扩容方式。

### 测试

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/frontend.test.cjs
```

测试使用模拟上游，不消费模型额度。本地浏览器已验证：跳过打字、失败恢复、刷新续玩、战斗重试不重复行动；390×844视口无横向溢出。真实模型调用和腾讯云部署验收仍需在服务器完成。

### 腾讯云更新要求

先核实实际进程、工作目录、启动方式和端口，不要直接运行第二个进程占用3000端口。
备份旧版代码或镜像及现有配置；沿用原有API Key环境变量，不写入仓库或日志。
同时更新 `baiyang.html`、`server.py`、`api_limits.py`。Docker需重新构建，并挂载持久目录，例如 `-v baiyang-state:/app/.state`。
原生Python服务将 `LIMITS_DB_PATH` 指向部署目录外的持久路径；启动用户需有写权限。
先跑测试与临时端口健康检查，再切换服务；确认 `/healthz`、首页、一次正常剧情及限额响应。如验证失败，恢复旧代码/镜像和启动配置。服务端计数文件应保留。

`compose.yaml` 已配置命名持久卷和健康检查。原有 `render.yaml` 的 free 配置没有持久盘，不能保障重新部署后的每日计数，不作为本次腾讯云部署方案。不要用 `docker compose down -v` 删除额度计数卷。
