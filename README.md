# Telegram Broadcast Bot

Бот для массовой рассылки сообщений через Telegram с поддержкой нескольких аккаунтов.

## Возможности

- Рассылка сообщений через несколько Telegram аккаунтов
- Поддержка архивных и обычных чатов
- Управление аккаунтами через бот-интерфейс
- Статистика рассылок
- Защита от флуд-контроля
- Автоматическое управление сессиями

## Требования

- Python 3.8+
- Telegram API credentials (api_id, api_hash)
- Telegram Bot Token

## Установка

### 1. Клонирование и настройка окружения

```bash
cd /opt
git clone <your-repo-url> telegram-broadcast-bot
cd telegram-broadcast-bot

# Создание виртуального окружения
python3 -m venv venv
source venv/bin/activate

# Установка зависимостей
pip install -r requirements.txt
```

### 2. Настройка конфигурации

Скопируйте `.env` и заполните необходимые данные:

```bash
cp .env .env.local
nano .env.local
```

Обязательные параметры:

```env
# Токен бота от @BotFather
BOT_TOKEN=your_bot_token_here

# ID администраторов (через запятую)
ADMIN_IDS=123456789,987654321

# API credentials от https://my.telegram.org
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
```

### 3. Создание необходимых директорий

```bash
mkdir -p telethon_sessions logs broadcast_control account_histories
chmod 700 telethon_sessions
```

## Запуск

### Ручной запуск

```bash
source venv/bin/activate
python bot.py
```

### Запуск через systemd (рекомендуется)

1. Скопируйте service файл:

```bash
sudo cp telegram-broadcast-bot.service /etc/systemd/system/
```

2. Отредактируйте пути в service файле:

```bash
sudo nano /etc/systemd/system/telegram-broadcast-bot.service
```

3. Запустите сервис:

```bash
sudo systemctl daemon-reload
sudo systemctl enable telegram-broadcast-bot
sudo systemctl start telegram-broadcast-bot
```

4. Проверьте статус:

```bash
sudo systemctl status telegram-broadcast-bot
```

### Просмотр логов

```bash
# Логи systemd
sudo journalctl -u telegram-broadcast-bot -f

# Логи приложения
tail -f logs/bot.log
```

## Использование

1. Запустите бота командой `/start`
2. Добавьте аккаунт через "➕ Добавить аккаунт"
3. Запустите рассылку через "🚀 Запустить рассылку"

## Структура проекта

```
.
├── bot.py                  # Основной файл бота
├── messages.py             # Модуль рассылки сообщений
├── account_utils.py        # Утилиты для работы с аккаунтами
├── requirements.txt        # Зависимости Python
├── .env                    # Шаблон конфигурации
├── telethon_sessions/      # Сессии Telegram аккаунтов
├── logs/                   # Логи приложения
├── broadcast_control/      # Файлы управления рассылками
└── account_histories/      # История рассылок по аккаунтам
```

## Безопасность

- Файл `.env` содержит конфиденциальные данные - НЕ коммитьте его в git
- Директория `telethon_sessions/` содержит сессии - защитите её (chmod 700)
- Используйте только доверенные аккаунты для рассылок
- Регулярно проверяйте логи на подозрительную активность

## Обновление

```bash
cd /opt/telegram-broadcast-bot
git pull
source venv/bin/activate
pip install -r requirements.txt --upgrade
sudo systemctl restart telegram-broadcast-bot
```

## Устранение неполадок

### Бот не запускается

1. Проверьте логи: `sudo journalctl -u telegram-broadcast-bot -n 50`
2. Проверьте конфигурацию в `.env`
3. Убедитесь, что все зависимости установлены

### Ошибки при рассылке

1. Проверьте статус аккаунта через "🔍 Проверить статус"
2. Убедитесь, что аккаунт не заблокирован Telegram
3. Проверьте логи в `logs/bot.log`

### Флуд-контроль

Бот автоматически обрабатывает FloodWait ошибки. Если рассылка замедлилась - это нормально.

## Поддержка

При возникновении проблем проверьте:
- Логи приложения в `logs/bot.log`
- Логи systemd через `journalctl`
- Конфигурацию в `.env`
