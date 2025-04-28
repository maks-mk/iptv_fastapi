# Развертывание IPTV-FastAPI в Production на Ubuntu + Apache2

> **Внимание!** Интерфейс всегда в тёмной теме, полностью адаптивен для мобильных устройств. Нет автовоспроизведения последнего канала при загрузке. Кнопка "Обновить" плейлист даёт визуальную обратную связь (анимация, подсветка изменений). Для вызова списка каналов на мобильных используйте плавающую кнопку меню или кнопку в шапке.

---

Эта инструкция описывает шаги по развертыванию приложения IPTV-FastAPI в производственной среде на сервере Ubuntu с использованием Apache2 в качестве обратного прокси и Gunicorn для запуска FastAPI приложения.

## 1. Подготовка сервера

### 1.1. Обновление системы

Убедитесь, что ваша система Ubuntu обновлена:

```bash
sudo apt update
sudo apt upgrade -y
```

### 1.2. Установка необходимых пакетов

Установите Python, pip, venv, git и Apache2:

```bash
sudo apt install -y python3 python3-pip python3-venv git apache2
```

### 1.3. Установка Apache2 модулей

Включите необходимые модули Apache для работы обратного прокси:

```bash
sudo a2enmod proxy proxy_http rewrite headers
sudo systemctl restart apache2
```

## 2. Установка приложения

### 2.1. Клонирование репозитория

Клонируйте репозиторий проекта в подходящую директорию (например, `/var/www/iptv_fastapi`):

```bash
sudo git clone <ссылка-на-ваш-репозиторий> /var/www/iptv_fastapi
cd /var/www/iptv_fastapi
sudo chown -R www-data:www-data /var/www/iptv_fastapi # Установка прав для Apache/Gunicorn
```

### 2.2. Создание виртуального окружения

Создайте и активируйте виртуальное окружение Python:

```bash
sudo python3 -m venv venv
source venv/bin/activate
```

*(Примечание: При работе под `sudo` активация может быть не нужна или потребует другого подхода в зависимости от конфигурации системы. Обычно настройка прав доступа и запуск Gunicorn от имени `www-data` решает эту проблему.)*

### 2.3. Установка зависимостей

Установите зависимости проекта из `requirements.txt`:

```bash
sudo venv/bin/pip install -r requirements.txt
```

*(Убедитесь, что в `requirements.txt` есть `gunicorn`.)*

## 3. Настройка Gunicorn

Gunicorn будет использоваться для запуска вашего FastAPI приложения.

### 3.1. Проверка запуска Gunicorn

Убедитесь, что Gunicorn может запустить приложение. Перейдите в директорию проекта и выполните (замените `www-data` на пользователя, от имени которого будет работать Gunicorn, если он отличается):

```bash
sudo -u www-data /var/www/iptv_fastapi/venv/bin/gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app --bind 127.0.0.1:8000
```

- `-w 4`: Количество рабочих процессов (настройте в зависимости от ресурсов сервера, обычно `2 * CPU_cores + 1`).
- `-k uvicorn.workers.UvicornWorker`: Использует асинхронный рабочий процесс Uvicorn.
- `--bind 127.0.0.1:8000`: Привязка к локальному адресу и порту. Apache будет проксировать запросы на этот адрес.

Нажмите `Ctrl+C`, чтобы остановить Gunicorn.

### 3.2. Создание Systemd сервиса для Gunicorn

Создайте файл юнита systemd для управления Gunicorn:

```bash
sudo nano /etc/systemd/system/iptv-fastapi.service
```

Вставьте следующее содержимое, заменив `<ваш_пользователь>` (если вы не используете `www-data`) и пути, если необходимо:

```ini
[Unit]
Description=Gunicorn instance to serve IPTV-FastAPI
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/iptv_fastapi
Environment="PATH=/var/www/iptv_fastapi/venv/bin"
ExecStart=/var/www/iptv_fastapi/venv/bin/gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app --bind 127.0.0.1:8000
Restart=always

[Install]
WantedBy=multi-user.target
```

### 3.3. Запуск и включение сервиса Gunicorn

```bash
sudo systemctl start iptv-fastapi
sudo systemctl enable iptv-fastapi
```

Проверьте статус сервиса:

```bash
sudo systemctl status iptv-fastapi
```

## 4. Настройка Apache2 как обратного прокси

### 4.1. Создание файла конфигурации VirtualHost

Создайте новый файл конфигурации для вашего сайта в Apache:

```bash
sudo nano /etc/apache2/sites-available/iptv-fastapi.conf
```

Вставьте следующую конфигурацию, заменив `your_domain.com` на ваш домен или IP-адрес сервера:

```apache
<VirtualHost *:80>
    ServerName your_domain.com
    # ServerAlias www.your_domain.com # Раскомментируйте, если нужен www алиас

    # Настройка логов (опционально)
    ErrorLog ${APACHE_LOG_DIR}/iptv-error.log
    CustomLog ${APACHE_LOG_DIR}/iptv-access.log combined

    # Разрешение перезаписи URL (если используется)
    RewriteEngine On

    # Проксирование запросов к Gunicorn
    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/

    # Увеличение тайм-аутов для прокси (важно для стриминга)
    ProxyTimeout 600
    
    # Настройка заголовков для корректной работы прокси
    RequestHeader set X-Forwarded-Proto "http"
    RequestHeader set X-Forwarded-Port "80"

</VirtualHost>
```

### 4.2. Включение сайта и перезапуск Apache

Включите созданный сайт и перезапустите Apache:

```bash
sudo a2ensite iptv-fastapi.conf
sudo a2dissite 000-default.conf # Отключаем стандартный сайт, если он не нужен
sudo systemctl reload apache2 # Проверка конфигурации
sudo systemctl restart apache2 # Перезапуск Apache
```

## 5. Настройка брандмауэра

Если вы используете брандмауэр (например, `ufw`), разрешите трафик на порты 80 (HTTP) и 443 (HTTPS, если будете настраивать):

```bash
sudo ufw allow 'Apache Full'
sudo ufw enable
sudo ufw status
```

## 6. Проверка

Откройте ваш домен или IP-адрес в браузере. Вы должны увидеть интерфейс IPTV-плеера.

- Для мобильных устройств предусмотрены крупные кнопки вызова меню каналов.
- Кнопка "Обновить" всегда показывает анимацию и подсвечивает изменения (количество каналов, дата обновления).
- Автовоспроизведение канала при загрузке отключено — пользователь сам выбирает канал.
- Интерфейс всегда в тёмной теме, не зависит от настроек системы.

## 7. (Опционально) Настройка HTTPS с Let's Encrypt

Для использования HTTPS рекомендуется использовать Certbot с Let's Encrypt.

### 7.1. Установка Certbot

```bash
sudo apt install certbot python3-certbot-apache -y
```

### 7.2. Получение сертификата

Запустите Certbot для вашего домена (замените `your_domain.com`):

```bash
sudo certbot --apache -d your_domain.com #-d www.your_domain.com # Добавьте www, если нужно
```

Certbot автоматически изменит конфигурацию Apache для использования HTTPS и настроит автоматическое продление сертификатов.

После завершения работы Certbot, Apache будет автоматически перезагружен.

### 7.3. Обновление конфигурации Apache для HTTPS

Certbot создаст файл `/etc/apache2/sites-available/iptv-fastapi-le-ssl.conf`. Убедитесь, что заголовок `X-Forwarded-Proto` установлен в `https`:

```apache
# Внутри <VirtualHost *:443>
# ... другие директивы SSL ...

RequestHeader set X-Forwarded-Proto "https"
RequestHeader set X-Forwarded-Port "443"

ProxyPreserveHost On
ProxyPass / http://127.0.0.1:8000/
ProxyPassReverse / http://127.0.0.1:8000/
ProxyTimeout 600

# ... остальные настройки ...
```

Перезапустите Apache после внесения изменений:
```bash
sudo systemctl restart apache2
```

Теперь ваше приложение должно быть доступно по `https://your_domain.com`.

## 8. Устранение неполадок

- **Проверьте логи Gunicorn**: `sudo journalctl -u iptv-fastapi`
- **Проверьте логи Apache**: `/var/log/apache2/iptv-error.log` и `/var/log/apache2/iptv-access.log` (или стандартные пути, если вы их не меняли).
- **Проверьте статус сервиса Gunicorn**: `sudo systemctl status iptv-fastapi`
- **Проверьте конфигурацию Apache**: `sudo apache2ctl configtest`
- **Убедитесь, что порт 8000 не занят другим процессом**: `sudo ss -tulnp | grep 8000`
- **Проверьте настройки брандмауэра**: `sudo ufw status` 