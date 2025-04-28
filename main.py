from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
import re
import httpx  # type: ignore
from urllib.parse import urljoin, urlparse, unquote, quote_plus

app = FastAPI()

# Подключение статики
app.mount("/static", StaticFiles(directory="static"), name="static")

# Подключение шаблонов
templates = Jinja2Templates(directory="templates")

# Функция очистки URL от вложенных префиксов /proxy?url=
def sanitize_url(url):
    # Удаляем все вложенные /proxy?url= префиксы
    if not url:
        return ""
    
    while "proxy?url=" in url:
        match = re.search(r'/proxy\?url=([^&]+)', url)
        if match:
            url = unquote(match.group(1))
        else:
            break
    
    # Проверяем, что URL начинается с http:// или https://
    if not url.startswith(('http://', 'https://')):
        return None
    
    return url

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/proxy")
async def proxy_stream(url: str, request: Request):
    # Очищаем URL от вложенных proxy вызовов
    clean_url = sanitize_url(url)
    if not clean_url:
        return Response(content=f"Недопустимый URL: {url}", status_code=400)
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Origin': 'https://live-mirror-01.ott.tricolor.tv',
        'Referer': 'https://live-mirror-01.ott.tricolor.tv/',
    }
    
    # Получаем user-agent от клиента, если доступен
    if "user-agent" in request.headers:
        headers["User-Agent"] = request.headers["user-agent"]
        
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.get(clean_url, headers=headers)
        except Exception as e:
            return Response(content=f"Ошибка запроса: {str(e)}", status_code=400)
        
        # Получаем заголовки ответа
        resp_headers = {k: v for k, v in response.headers.items() 
                        if k.lower() not in ('content-encoding', 'content-length', 'transfer-encoding')}
        
        # Для m3u8 плейлистов нужно обрабатывать URL к сегментам
        content_type = response.headers.get('content-type', '').lower()
        if 'application/vnd.apple.mpegurl' in content_type or clean_url.endswith('.m3u8'):
            # Обработка m3u8 файла
            content = response.text
            base_url = clean_url.rsplit('/', 1)[0] + '/'
            
            # Обработка ссылок внутри m3u8 файла
            lines = content.splitlines()
            processed_lines = []
            
            for line in lines:
                # Пропускаем комментарии и пустые строки при обработке ссылок
                if line.startswith('#') and 'URI=' not in line and 'http' not in line:
                    processed_lines.append(line)
                    continue
                    
                # Обрабатываем URI в ключах
                if 'URI=' in line:
                    line = re.sub(r'URI="([^"]+)"', 
                                 lambda m: f'URI="/proxy?url={quote_plus(urljoin(base_url, m.group(1)))}"', 
                                 line)
                    processed_lines.append(line)
                    continue
                
                # Обрабатываем HTTP URLs в комментариях
                if line.startswith('#') and ('http://' in line or 'https://' in line):
                    line = re.sub(r'(https?://[^"\s,]+)', 
                                 lambda m: f'/proxy?url={quote_plus(m.group(1))}', 
                                 line)
                    processed_lines.append(line)
                    continue
                
                # Обрабатываем URL сегментов (ts или m3u8 файлы)
                if not line.startswith('#') and (line.strip().endswith('.ts') or 
                                               line.strip().endswith('.m3u8') or
                                               '.ts?' in line or
                                               '.m3u8?' in line):
                    # Если это абсолютный URL
                    if line.startswith('http'):
                        processed_lines.append(f'/proxy?url={quote_plus(line)}')
                    else:
                        # Это относительный URL
                        full_url = urljoin(base_url, line)
                        processed_lines.append(f'/proxy?url={quote_plus(full_url)}')
                    continue
                
                # Неизмененная строка
                processed_lines.append(line)
            
            content = '\n'.join(processed_lines)
            return Response(content=content, media_type="application/vnd.apple.mpegurl")
        else:
            # Для остальных типов контента просто проксируем как есть
            return StreamingResponse(
                content=response.iter_bytes(),
                status_code=response.status_code,
                headers=resp_headers
            )

@app.get("/channels")
async def get_channels():
    playlist_file = "local.m3u"
    channels = []
    categories = {}

    if not os.path.exists(playlist_file):
        return JSONResponse(content={"channels": [], "categories": {}})

    with open(playlist_file, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    channel = None
    for line in lines:
        line = line.strip()
        if line.startswith('#EXTINF:'):
            group = "Без категории"
            match = re.search(r'group-title="([^"]+)"', line)
            if match:
                group = match.group(1)
            parts = line.split(',', 1)
            if len(parts) > 1:
                channel = {
                    "name": parts[1].strip(),
                    "group": group
                }
        elif channel and not line.startswith('#'):
            channel["url"] = line
            channels.append(channel)
            if group not in categories:
                categories[group] = []
            categories[group].append(channel)
            channel = None

    return JSONResponse(content={"channels": channels, "categories": categories})
