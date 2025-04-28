from fastapi import FastAPI, Request, status, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
import re
import httpx  # type: ignore
import logging
import time
import io
from urllib.parse import urljoin, urlparse, unquote, quote_plus
from functools import lru_cache
from typing import Optional, Dict, Any, Tuple

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log')
    ]
)
logger = logging.getLogger("iptv")

# Устанавливаем уровень WARNING для файлового обработчика
for handler in logger.handlers:
    if isinstance(handler, logging.FileHandler):
        handler.setLevel(logging.WARNING)

app = FastAPI()

# Подключение статики
app.mount("/static", StaticFiles(directory="static"), name="static")

# Подключение шаблонов
templates = Jinja2Templates(directory="templates")

# Кэш для URL запросов
URL_CACHE: Dict[str, Tuple[int, Any]] = {}
CACHE_TTL = 180  # Время жизни кэша в секундах (увеличено с 30 до 180 секунд)

# URL удаленного плейлиста IPTV
PLAYLIST_URL = "https://gitlab.com/iptv135435/iptvshared/raw/main/IPTV_SHARED.m3u"
# Как часто обновлять плейлист (в секундах) - каждые 6 часов
PLAYLIST_REFRESH_TIME = 6 * 60 * 60
# Время последнего обновления плейлиста
LAST_PLAYLIST_UPDATE = 0
# Кэш плейлиста
PLAYLIST_CACHE = None

# Функция очистки URL от вложенных префиксов /proxy?url=
def sanitize_url(url: Optional[str]) -> Optional[str]:
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
    logger.info(f"Доступ к главной странице: {request.client.host}")
    return templates.TemplateResponse("index.html", {"request": request})

# Функция ручной обработки редиректов
async def follow_redirects_manually(client, url, headers, max_redirects=8):
    """Вручную следует по редиректам до макс. количества переходов"""
    for i in range(max_redirects):
        response = await client.get(url, headers=headers)
        
        # Если это не редирект, возвращаем ответ
        if response.status_code < 300 or response.status_code >= 400:
            return response
            
        # Получаем новый URL из заголовка Location
        new_url = response.headers.get('location')
        if not new_url:
            return response
            
        # Если относительный URL, преобразуем в абсолютный
        if not new_url.startswith(('http://', 'https://')):
            parsed_url = urlparse(url)
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            new_url = urljoin(base_url, new_url)
            
        # Обновляем URL и пробуем снова
        url = new_url
        
    # Если достигли максимального числа редиректов
    raise HTTPException(status_code=310, detail=f"Слишком много редиректов (макс: {max_redirects})")

# Функция для получения контента по URL
async def fetch_content(url: str, user_agent: str) -> Tuple[Any, Dict, int]:
    """
    Делает запрос к внешнему ресурсу
    Возвращает (контент, заголовки, статус-код)
    """
    # Формируем заголовки запроса    
    headers = {
        'User-Agent': user_agent,
        'Accept': '*/*',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Origin': 'https://live-mirror-01.ott.tricolor.tv',
        'Referer': 'https://live-mirror-01.ott.tricolor.tv/',
    }
    
    # Выполняем запрос
    try:
        # Создаем клиент с таймаутом, но без follow_redirects (так как он не везде работает)
        async with httpx.AsyncClient(timeout=30.0) as client:
            logger.info(f"Запрос к внешнему ресурсу: {url}")
            
            # Сначала пробуем прямой запрос
            try:
                # Делаем запрос и вручную обрабатываем редиректы
                response = await follow_redirects_manually(client, url, headers, max_redirects=8)
                
                # Получаем заголовки ответа
                resp_headers = {k: v for k, v in response.headers.items() 
                                if k.lower() not in ('content-encoding', 'content-length', 'transfer-encoding')}
                
                return response, resp_headers, response.status_code
                
            except Exception as e:
                logger.warning(f"Ошибка при следовании редиректам: {str(e)}, пробуем прямой запрос")
                # Если что-то пошло не так при обработке редиректов, делаем прямой запрос
                response = await client.get(url, headers=headers)
                
                # Получаем заголовки ответа
                resp_headers = {k: v for k, v in response.headers.items() 
                                if k.lower() not in ('content-encoding', 'content-length', 'transfer-encoding')}
                
                return response, resp_headers, response.status_code
            
    except httpx.HTTPStatusError as e:
        logger.error(f"Ошибка HTTP статуса при запросе {url}: {str(e)}")
        raise HTTPException(status_code=e.response.status_code, detail=f"Ошибка удаленного сервера: {str(e)}")
    except httpx.TimeoutException:
        logger.error(f"Таймаут при запросе {url}")
        raise HTTPException(status_code=504, detail="Превышено время ожидания ответа от сервера")
    except httpx.RequestError as e:
        logger.error(f"Ошибка запроса к {url}: {str(e)}")
        raise HTTPException(status_code=502, detail=f"Ошибка подключения: {str(e)}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка при запросе {url}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка: {str(e)}")

# Функция для получения кэшированного контента
async def fetch_cached_content(url: str, user_agent: str) -> Tuple[Any, Dict, int]:
    """
    Кэширующая обертка для запросов к внешним ресурсам
    Возвращает (контент, заголовки, статус-код)
    """
    # Проверяем кэш
    current_time = time.time()
    cache_key = f"{url}:{user_agent}"
    
    if cache_key in URL_CACHE:
        timestamp, cache_data = URL_CACHE[cache_key]
        if current_time - timestamp < CACHE_TTL:
            logger.debug(f"Используем кэшированный ответ для {url}")
            return cache_data
    
    # Получаем содержимое через некэшированную функцию
    result = await fetch_content(url, user_agent)
    
    # Сохраняем в кэш
    URL_CACHE[cache_key] = (current_time, result)
    
    return result

# Функция загрузки удаленного плейлиста
async def fetch_remote_playlist():
    """Загружает плейлист с удаленного URL и возвращает его содержимое"""
    global LAST_PLAYLIST_UPDATE, PLAYLIST_CACHE
    
    current_time = time.time()
    
    # Проверяем, нужно ли обновлять плейлист
    if PLAYLIST_CACHE is not None and current_time - LAST_PLAYLIST_UPDATE < PLAYLIST_REFRESH_TIME:
        logger.debug("Используем кэшированный плейлист")
        return PLAYLIST_CACHE
    
    try:
        logger.info(f"Загрузка удаленного плейлиста: {PLAYLIST_URL}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(PLAYLIST_URL)
            
            if response.status_code != 200:
                logger.error(f"Ошибка загрузки плейлиста: HTTP {response.status_code}")
                if PLAYLIST_CACHE:  # Используем старый кэш, если есть
                    return PLAYLIST_CACHE
                raise HTTPException(status_code=response.status_code, 
                                   detail=f"Ошибка загрузки плейлиста: HTTP {response.status_code}")
            
            # Обновляем кэш и время последнего обновления
            PLAYLIST_CACHE = response.text
            LAST_PLAYLIST_UPDATE = current_time
            
            logger.info(f"Плейлист успешно загружен, размер: {len(PLAYLIST_CACHE)} байт")
            return PLAYLIST_CACHE
            
    except Exception as e:
        logger.error(f"Ошибка при загрузке плейлиста: {str(e)}")
        if PLAYLIST_CACHE:  # Используем старый кэш, если есть
            return PLAYLIST_CACHE
        raise HTTPException(status_code=500, 
                           detail=f"Не удалось загрузить плейлист: {str(e)}")

@app.get("/proxy")
async def proxy_stream(url: str, request: Request):
    start_time = time.time()
    client_ip = request.client.host
    
    # Очищаем URL от вложенных proxy вызовов
    clean_url = sanitize_url(url)
    if not clean_url:
        logger.warning(f"Недопустимый URL: {url} от {client_ip}")
        return Response(content=f"Недопустимый URL: {url}", status_code=400)
    
    # Добавляем параметр для предотвращения кэширования CDN/edge серверами
    # Если URL уже содержит параметры запроса
    cache_buster = int(time.time())
    if '?' in clean_url:
        clean_url = f"{clean_url}&_nocache={cache_buster}"
    else:
        clean_url = f"{clean_url}?_nocache={cache_buster}"
    
    # Получаем user-agent от клиента, если доступен
    user_agent = request.headers.get("user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    try:
        # Получаем содержимое с использованием кэша
        response, resp_headers, status_code = await fetch_cached_content(clean_url, user_agent)
        
        # Для m3u8 плейлистов нужно обрабатывать URL к сегментам
        content_type = response.headers.get('content-type', '').lower()
        
        # HLS (m3u8) плейлисты
        if 'application/vnd.apple.mpegurl' in content_type or clean_url.endswith('.m3u8'):
            logger.debug(f"Обрабатываем HLS плейлист: {clean_url}")
            content = response.text
            base_url = clean_url.rsplit('/', 1)[0] + '/'
            
            # Обработка ссылок внутри m3u8 файла
            lines = content.splitlines()
            processed_lines = []
            
            # Проверяем, является ли это мастер-плейлистом или плейлистом фрагментов
            is_master_playlist = any('#EXT-X-STREAM-INF' in line for line in lines)
            
            # Устанавливаем более длинный целевой период буферизации для плеера
            if not is_master_playlist:
                # Модифицируем плейлист с фрагментами для улучшения буферизации
                has_target_duration = False
                for line in lines:
                    if line.startswith('#EXT-X-TARGETDURATION:'):
                        # Извлекаем текущее значение и увеличиваем его для лучшей буферизации
                        try:
                            current_duration = int(line.split(':')[1].strip())
                            # Увеличиваем целевую продолжительность для лучшей буферизации
                            processed_lines.append(f'#EXT-X-TARGETDURATION:{current_duration * 2}')
                            has_target_duration = True
                            continue
                        except (ValueError, IndexError):
                            # Если не удалось преобразовать, оставляем как есть
                            processed_lines.append(line)
                            has_target_duration = True
                            continue
                    
                    # Добавляем параметр для более длинного буфера, если его нет
                    if line.startswith('#EXT-X-PLAYLIST-TYPE:'):
                        processed_lines.append(line)
                        continue
                        
                    # Если это строка с #EXTINF, прокси не требуется
                    if line.startswith('#EXTINF:'):
                        processed_lines.append(line)
                        continue
                        
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
                
                # Добавляем тип плейлиста VOD, если его нет, для лучшей буферизации
                if not any('#EXT-X-PLAYLIST-TYPE:VOD' in line for line in processed_lines) and not any('#EXT-X-ENDLIST' in line for line in processed_lines):
                    # Вставляем после заголовка, но перед метаданными сегментов
                    header_end_index = next((i for i, line in enumerate(processed_lines) 
                                           if not line.startswith('#') or line.startswith('#EXTINF')), 0)
                    processed_lines.insert(header_end_index, '#EXT-X-PLAYLIST-TYPE:VOD')
            else:
                # Обработка мастер-плейлиста проще, так как в нем только ссылки на другие плейлисты
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
                    
                    # Обрабатываем URL фрагментов плейлистов
                    if not line.startswith('#') and line.strip():
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
            
            # Замеряем время обработки и логируем
            process_time = time.time() - start_time
            logger.info(f"HLS прокси обработан за {process_time:.3f}с: {clean_url} -> {status_code}")
            
            # Устанавливаем правильные заголовки для кэширования в браузере
            custom_headers = {
                'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                'Pragma': 'no-cache',
                'Expires': '0',
                'Content-Type': 'application/vnd.apple.mpegurl'
            }
            
            return Response(content=content, media_type="application/vnd.apple.mpegurl", headers=custom_headers)
        
        # DASH (mpd) плейлисты
        elif 'application/dash+xml' in content_type or clean_url.endswith('.mpd'):
            logger.debug(f"Обрабатываем DASH плейлист: {clean_url}")
            content = response.text
            base_url = clean_url.rsplit('/', 1)[0] + '/'
            
            # Заменяем URLs в MPD файле на проксированные версии
            # Заменяем адреса в SegmentTemplate
            content = re.sub(r'(initialization|media)="([^"]+)"', 
                            lambda m: f'{m.group(1)}="/proxy?url={quote_plus(urljoin(base_url, m.group(2)))}"',
                            content)
            
            # Заменяем абсолютные URLs
            content = re.sub(r'(https?://[^"\s]+\.m4s|https?://[^"\s]+\.mp4)', 
                            lambda m: f'/proxy?url={quote_plus(m.group(1))}',
                            content)
            
            # Замеряем время обработки и логируем
            process_time = time.time() - start_time
            logger.info(f"DASH прокси обработан за {process_time:.3f}с: {clean_url} -> {status_code}")
            
            # Добавляем заголовки для предотвращения кэширования
            custom_headers = {
                'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                'Pragma': 'no-cache',
                'Expires': '0',
                'Content-Type': 'application/dash+xml'
            }
            
            return Response(content=content, media_type="application/dash+xml", headers=custom_headers)
            
        else:
            # Для остальных типов контента просто проксируем как есть
            process_time = time.time() - start_time
            logger.info(f"Прокси поток обработан за {process_time:.3f}с: {clean_url} -> {status_code}")
            
            # Добавляем заголовки для стриминг-контента
            if content_type and ('video/' in content_type or '/ts' in content_type or '/mp4' in content_type):
                # Добавляем заголовки для стриминг-контента
                for key in list(resp_headers.keys()):
                    if key.lower() in ('cache-control', 'pragma', 'expires'):
                        del resp_headers[key]
                        
                resp_headers.update({
                    'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                    'Pragma': 'no-cache',
                    'Expires': '0'
                })
            
            return StreamingResponse(
                content=response.iter_bytes(),
                status_code=status_code,
                headers=resp_headers
            )
    
    except HTTPException as e:
        # Перехватываем и логируем HTTP ошибки
        logger.error(f"Ошибка прокси для {clean_url}: {e.detail}")
        return Response(
            content=f"Ошибка при обработке запроса: {e.detail}",
            status_code=e.status_code
        )
    except Exception as e:
        # Перехватываем все другие ошибки
        logger.error(f"Неизвестная ошибка при прокси {clean_url}: {str(e)}", exc_info=True)
        return Response(
            content=f"Внутренняя ошибка сервера: {str(e)}",
            status_code=500
        )

@app.get("/api/channels")
async def get_channels():
    try:
        logger.info(f"Загрузка списка каналов из удаленного плейлиста")
        
        # Получаем содержимое плейлиста из кэша или загружаем с URL
        playlist_content = await fetch_remote_playlist()
        
        # Разбираем плейлист
        channels = []
        categories = {}
        channel_id = 0
        
        lines = playlist_content.splitlines()
        
        channel = None
        for line in lines:
            line = line.strip()
            if line.startswith('#EXTINF:'):
                group = "Без категории"
                match = re.search(r'group-title="([^"]+)"', line)
                if match:
                    group = match.group(1)
                
                logo = None
                logo_match = re.search(r'tvg-logo="([^"]+)"', line)
                if logo_match:
                    logo = logo_match.group(1)
                
                parts = line.split(',', 1)
                if len(parts) > 1:
                    channel_id += 1
                    channel = {
                        "id": str(channel_id),
                        "name": parts[1].strip(),
                        "group": group,
                        "logo": logo
                    }
            elif channel and not line.startswith('#'):
                channel["url"] = line
                channels.append(channel)
                if group not in categories:
                    categories[group] = []
                categories[group].append(channel)
                channel = None

        logger.info(f"Загружено {len(channels)} каналов в {len(categories)} категориях")
        return JSONResponse(content={
            "channels": channels, 
            "categories": categories,
            "last_update": LAST_PLAYLIST_UPDATE  # Добавляем время последнего обновления
        })
    
    except Exception as e:
        logger.error(f"Ошибка при загрузке плейлиста: {str(e)}", exc_info=True)
        return JSONResponse(
            content={"error": f"Ошибка при загрузке плейлиста: {str(e)}"}, 
            status_code=500
        )

@app.get("/api/stream/{channel_id}")
async def stream_channel(channel_id: str, request: Request):
    try:
        # Получаем плейлист
        playlist_content = await fetch_remote_playlist()
        
        # Разбираем плейлист для поиска канала по ID
        channels = []
        current_id = 0
        
        lines = playlist_content.splitlines()
        
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
                    current_id += 1
                    if str(current_id) == channel_id:
                        channel = {"id": str(current_id), "name": parts[1].strip()}
            elif channel and not line.startswith('#'):
                channel["url"] = line
                # Проксируем поток через наш прокси
                return RedirectResponse(url=f"/proxy?url={quote_plus(channel['url'])}")
        
        # Если канал не найден
        raise HTTPException(status_code=404, detail="Канал не найден")
    
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка при стриминге канала: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")

# Принудительное обновление плейлиста
@app.get("/refresh-playlist")
async def refresh_playlist(request: Request):
    try:
        client_ip = request.client.host
        # Проверяем локальный запрос или с той же сети
        if client_ip.startswith("127.0.0.1") or client_ip.startswith("192.168.") or client_ip == "::1":
            global LAST_PLAYLIST_UPDATE
            LAST_PLAYLIST_UPDATE = 0  # Сбрасываем время обновления, чтобы принудительно обновить
            
            # Загружаем новый плейлист
            playlist_content = await fetch_remote_playlist()
            
            logger.info(f"Плейлист принудительно обновлен, размер: {len(playlist_content)} байт")
            return JSONResponse({"status": "success", "message": f"Плейлист обновлен, получено {len(playlist_content)} байт"})
        else:
            logger.warning(f"Попытка обновить плейлист с неавторизованного IP: {client_ip}")
            return JSONResponse(
                {"status": "error", "message": "Доступ запрещен"}, 
                status_code=403
            )
    except Exception as e:
        logger.error(f"Ошибка при обновлении плейлиста: {str(e)}")
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500
        )

# Очистка кэша
@app.get("/clear-cache")
async def clear_cache(request: Request):
    try:
        client_ip = request.client.host
        # Проверяем локальный запрос или с той же сети
        if client_ip.startswith("127.0.0.1") or client_ip.startswith("192.168.") or client_ip == "::1":
            cache_size = len(URL_CACHE)
            URL_CACHE.clear()
            logger.info(f"Кэш очищен ({cache_size} элементов)")
            return JSONResponse({"status": "success", "message": f"Кэш очищен ({cache_size} элементов)"})
        else:
            logger.warning(f"Попытка очистить кэш с неавторизованного IP: {client_ip}")
            return JSONResponse(
                {"status": "error", "message": "Доступ запрещен"}, 
                status_code=403
            )
    except Exception as e:
        logger.error(f"Ошибка при очистке кэша: {str(e)}")
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500
        )

# Проверка работоспособности сервиса
@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": time.time()}
