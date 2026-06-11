FROM python:3.11-slim

WORKDIR /app
ENV PORT=8080

COPY baiyang.html server.py ./

EXPOSE 8080
CMD ["python", "server.py"]
