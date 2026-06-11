FROM python:3.11-slim
COPY baiyang.html server.py ./
CMD ["python", "server.py"]
