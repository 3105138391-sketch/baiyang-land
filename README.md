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
