FROM mcr.microsoft.com/playwright/python:v1.64.1-jammy
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV HEADLESS=true
CMD ["python", "-c", "import os,threading,runpy,http.server as h;H=type('H',(h.BaseHTTPRequestHandler,),{'do_GET':lambda s:(s.send_response(200),s.end_headers(),s.wfile.write(b'ok')),'do_HEAD':lambda s:(s.send_response(200),s.end_headers()),'log_message':lambda s,*a:None});threading.Thread(target=h.HTTPServer(('0.0.0.0',int(os.environ.get('PORT','10000'))),H).serve_forever,daemon=True).start();runpy.run_path('main.py',run_name='__main__')"]
