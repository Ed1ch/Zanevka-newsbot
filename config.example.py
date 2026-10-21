# =========================
# ФАЙЛЫ
# =========================

EML_FILE = r"ЗГП.eml"
OUTPUT_DIR = r"extracted_images"

# =========================
# WORDPRESS
# =========================

WP_URL = "https://www.example.com"
WP_USERNAME = "your_username"
WP_APP_PASSWORD = "your_application_password"
WP_API_TIMEOUT = 30

# =========================
# ПУБЛИКАЦИЯ
# =========================

POST_STATUS = "draft"
GALLERY_COLUMNS = 3
GALLERY_SIZE = "medium"

# =========================
# VK
# =========================

VK_ACCESS_TOKEN = "your_vk_access_token"
VK_API_VERSION = "5.199"
VK_API_TIMEOUT = 15

# =========================
# IMAP / MAIL DOWNLOADER
# =========================

# Оставьте фактические значения вашего тестового ящика.
IMAP_HOST = "YOUR_IMAP_SERVER"
IMAP_PORT = 993

IMAP_USERNAME = "news@zanevkaorg.ru"
IMAP_PASSWORD = "YOUR_IMAP_PASSWORD"

IMAP_MAILBOX = "INBOX"
IMAP_TRASH = "Trash"

# Ищется вхождение строки в Subject.
IMAP_SUBJECT = "ЗГП"

# Папка создаётся автоматически.
IMAP_OUTPUT_DIR = r".\downloaded_eml"

# Таймаут одной IMAP-сетевой операции, секунд.
IMAP_TIMEOUT = 120

# Количество реальных попыток скачать одно письмо.
IMAP_RETRY_COUNT = 3

# Пауза между попытками, секунд.
IMAP_RETRY_DELAY = 5

# =========================
# ЖУРНАЛЫ ЗАПУСКОВ
# =========================

# Относительный путь считается от каталога проекта, а не от cwd cron.
# Существующий config.py может не содержать эти переменные: значения ниже
# также используются по умолчанию в log.py.
LOG_DIR = "logs"
LOG_RETENTION_DAYS = 30
LOG_MAX_TOTAL_MB = 200


