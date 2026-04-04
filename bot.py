#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Broadcast Bot - Main Bot Interface
Secure multi-account broadcast system for Telegram
"""

import os
import asyncio
import subprocess
import logging
import json
import inspect
import shutil
import re
from datetime import datetime
from functools import wraps
from contextlib import asynccontextmanager
from typing import Dict, Any, Tuple, List, Optional
from pathlib import Path

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ==============================
# Configuration and Constants
# ==============================

load_dotenv()

# Timeouts and limits
TIMEOUT_NETWORK = 30  # seconds
TIMEOUT_PROCESS = 7200  # seconds (2 hours)
MAX_MESSAGE_LENGTH = 4096  # Telegram limit
MAX_PHONE_LENGTH = 20
MIN_PHONE_LENGTH = 7
MAX_CODE_LENGTH = 10
MIN_CODE_LENGTH = 4
MAX_NAME_LENGTH = 50

# Directories (using hidden directory for sessions)
SESSION_DIR = os.getenv("SESSION_DIR", ".sessions")
LOGS_DIR = os.getenv("LOGS_DIR", "logs")
CONTROL_DIR = os.getenv("CONTROL_DIR", "broadcast_control")

# Files
STATS_FILE = "broadcast_stats.json"
BROADCAST_LOCKS_FILE = "broadcast_locks.json"
ACCOUNT_NAMES_FILE = os.getenv("ACCOUNT_NAMES_FILE", "account_names.json")

# Create directories with proper permissions
for directory in [SESSION_DIR, LOGS_DIR, CONTROL_DIR, "account_histories"]:
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    except OSError as e:
        print(f"Error creating directory {directory}: {e}")
        raise SystemExit(1)

# Logging configuration
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, os.getenv("LOG_FILE", "bot.log"))),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("broadcast_bot")

# Validate environment variables
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")

if not API_TOKEN:
    logger.error("BOT_TOKEN not found in .env file")
    raise SystemExit(1)

try:
    ADMIN_IDS = [
        int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip().isdigit()
    ]
    if not ADMIN_IDS:
        logger.error("No valid ADMIN_IDS found in .env file")
        raise SystemExit(1)
except ValueError as e:
    logger.error(f"Invalid ADMIN_IDS format: {e}")
    raise SystemExit(1)

# Initialize bot components
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Global process registry
ACTIVE_BROADCASTS: Dict[str, subprocess.Popen] = {}

# ==============================
# FSM States
# ==============================


class BroadcastStates(StatesGroup):
    choosing_account = State()
    choosing_mode = State()
    entering_text = State()
    confirming_broadcast = State()


class AddAccountStates(StatesGroup):
    entering_phone = State()
    entering_code = State()
    entering_password = State()


class ManageAccountStates(StatesGroup):
    choosing_action = State()
    selecting_account = State()
    confirming_delete = State()
    editing_name = State()


# ==============================
# Custom Exceptions
# ==============================


class BotError(Exception):
    """Base exception for bot-related errors"""

    def __init__(self, message: str, user_message: Optional[str] = None):
        super().__init__(message)
        self.user_message = user_message or message


class ValidationError(BotError):
    """Raised when input validation fails"""

    pass


class NetworkError(BotError):
    """Raised when network operations fail"""

    pass


class ProcessError(BotError):
    """Raised when subprocess operations fail"""

    pass


class ConfigError(BotError):
    """Raised when configuration is invalid"""

    pass


# ==============================
# Error Handling
# ==============================


async def handle_error(
    message: types.Message, error: Exception, context: str = ""
) -> None:
    """Centralized error handler with logging and user notification"""
    err_id = f"ERR_{int(datetime.now().timestamp())}"

    if isinstance(error, BotError):
        user_msg = error.user_message
        log_msg = f"[{err_id}] {context}: {error}"
        logger.warning(log_msg)
    else:
        user_msg = f"Internal error occurred. ID: {err_id}"
        log_msg = f"[{err_id}] {context}: {repr(error)}"
        logger.error(log_msg, exc_info=True)

    try:
        # Note: get_main_menu() will be defined in Part 3
        await message.answer(f"❌ {user_msg}")
    except Exception as e:
        logger.error(f"Failed to send error message: {e}")


# ==============================
# Telethon Client Context Manager
# ==============================


@asynccontextmanager
async def telethon_client(session_path: str, api_id: int, api_hash: str):
    """Context manager for Telethon client with proper cleanup"""
    from telethon import TelegramClient

    client = TelegramClient(session_path, api_id, api_hash)
    try:
        await asyncio.wait_for(client.connect(), timeout=TIMEOUT_NETWORK)
        yield client
    finally:
        try:
            await client.disconnect()
        except Exception as e:
            logger.warning(f"Error disconnecting Telethon client: {e}")


# ==============================
# Utility Functions
# ==============================


def get_environment_config() -> Tuple[int, str]:
    """Get validated environment configuration"""
    api_id_str = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")

    if not api_id_str:
        raise ConfigError("TELEGRAM_API_ID not configured")

    if not api_hash:
        raise ConfigError("TELEGRAM_API_HASH not configured")

    try:
        api_id = int(api_id_str)
    except ValueError:
        raise ConfigError("TELEGRAM_API_ID must be a valid integer")

    return api_id, api_hash


# ==============================
# Input Validation Functions
# ==============================


def validate_phone(phone: str) -> Tuple[bool, str]:
    """Validate phone number format"""
    if not phone or not isinstance(phone, str):
        return False, "Phone number is required"

    phone = phone.strip()
    if not phone.startswith("+"):
        return False, "Phone number must start with +"

    # Remove formatting
    digits = re.sub(r"[^\d]", "", phone[1:])

    if not digits:
        return False, "Phone number must contain digits"

    if len(digits) < MIN_PHONE_LENGTH:
        return False, f"Phone number too short (minimum {MIN_PHONE_LENGTH} digits)"

    if len(phone) > MAX_PHONE_LENGTH:
        return False, f"Phone number too long (maximum {MAX_PHONE_LENGTH} characters)"

    # Basic format validation
    if not re.match(r"^\+\d{7,15}$", f"+{digits}"):
        return False, "Invalid phone number format"

    return True, ""


def validate_code(code: str) -> Tuple[bool, str]:
    """Validate verification code"""
    if not code or not isinstance(code, str):
        return False, "Verification code is required"

    # Clean code
    clean_code = re.sub(r"[^\d]", "", code.strip())

    if not clean_code:
        return False, "Code must contain only digits"

    if len(clean_code) < MIN_CODE_LENGTH or len(clean_code) > MAX_CODE_LENGTH:
        return False, f"Code must be {MIN_CODE_LENGTH}-{MAX_CODE_LENGTH} digits long"

    return True, clean_code


def validate_account_name(name: str) -> Tuple[bool, str]:
    """Validate account display name"""
    if not name or not isinstance(name, str):
        return False, "Account name is required"

    name = name.strip()
    if not name:
        return False, "Account name cannot be empty"

    if len(name) > MAX_NAME_LENGTH:
        return False, f"Account name too long (maximum {MAX_NAME_LENGTH} characters)"

    # Check for potentially dangerous characters
    if re.search(r'[<>:"\\|?*\x00-\x1f]', name):
        return False, "Account name contains invalid characters"

    return True, ""


def validate_message_text(text: str) -> Tuple[bool, str]:
    """Validate broadcast message text"""
    if not text or not isinstance(text, str):
        return False, "Message text is required"

    text = text.strip()
    if not text:
        return False, "Message cannot be empty"

    if len(text) > MAX_MESSAGE_LENGTH:
        return False, f"Message too long (maximum {MAX_MESSAGE_LENGTH} characters)"

    return True, ""


# ==============================
# Data Management with Error Handling
# ==============================


def safe_load_json(filepath: str) -> Dict[str, Any]:
    """Safely load JSON file with error handling"""
    if not filepath:
        return {}

    try:
        if not os.path.exists(filepath):
            return {}

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}

    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        logger.error(f"Error loading JSON from {filepath}: {e}")
        # Create backup of corrupted file
        try:
            backup_path = f"{filepath}.backup_{int(datetime.now().timestamp())}"
            shutil.copy2(filepath, backup_path)
            logger.info(f"Created backup: {backup_path}")
        except Exception:
            pass
        return {}


def safe_save_json(filepath: str, data: Dict[str, Any]) -> bool:
    """Safely save JSON file with atomic write"""
    if not filepath:
        return False

    try:
        # Atomic write using temporary file
        temp_path = f"{filepath}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Atomic replace
        os.replace(temp_path, filepath)
        return True

    except (OSError, TypeError, ValueError) as e:
        logger.error(f"Error saving JSON to {filepath}: {e}")
        try:
            os.remove(temp_path)
        except Exception:
            pass
        return False


# ==============================
# Account Management
# ==============================


def load_account_names() -> Dict[str, str]:
    """Load account display names"""
    return safe_load_json(ACCOUNT_NAMES_FILE)


def save_account_names(data: Dict[str, str]) -> bool:
    """Save account display names"""
    return safe_save_json(ACCOUNT_NAMES_FILE, data)


def get_account_display_name(session_name: str) -> str:
    """Get friendly display name for account"""
    names = load_account_names()
    return names.get(session_name, session_name)


def extract_session_name_from_display(display_text: str) -> str:
    """Extract session name from display text"""
    if not display_text:
        return ""

    text = display_text.strip()
    if text.startswith("👤 "):
        friendly = text[2:].strip()
        names = load_account_names()
        for session_name, fname in names.items():
            if fname == friendly:
                return session_name
        return friendly
    return text


def get_session_accounts() -> List[str]:
    """Get list of available session accounts"""
    try:
        if not os.path.exists(SESSION_DIR):
            return []

        accounts = []
        for filename in os.listdir(SESSION_DIR):
            if filename.endswith(".session"):
                accounts.append(filename[:-8])  # Remove .session extension

        return sorted(accounts)
    except OSError as e:
        logger.error(f"Error reading session directory: {e}")
        return []


# ==============================
# Session Cleanup Helper
# ==============================


def cleanup_failed_session(session_name: str) -> bool:
    """
    Clean up session file and related data for failed authentication

    Args:
        session_name: Session name (without .session extension)

    Returns:
        True if cleanup was successful
    """
    try:
        session_path = os.path.join(SESSION_DIR, f"{session_name}.session")

        # Remove session file if exists
        if os.path.exists(session_path):
            os.remove(session_path)
            logger.info(f"Removed failed session file: {session_name}.session")

        # Remove from account names if exists
        names = load_account_names()
        if session_name in names:
            del names[session_name]
            save_account_names(names)
            logger.info(f"Removed {session_name} from account names")

        # Clear history files
        clear_account_history(session_name)

        return True
    except Exception as e:
        logger.error(f"Failed to cleanup session {session_name}: {e}")
        return False


# ==============================
# Statistics Management
# ==============================


def load_stats() -> Dict[str, Any]:
    """Load broadcast statistics"""
    stats = safe_load_json(STATS_FILE)

    # Ensure required keys exist
    default_stats = {"broadcasts": [], "total_messages": 0, "successful_broadcasts": 0}

    for key, default_value in default_stats.items():
        if key not in stats:
            stats[key] = default_value

    return stats


def save_stats(stats: Dict[str, Any]) -> bool:
    """Save broadcast statistics"""
    return safe_save_json(STATS_FILE, stats)


# ==============================
# Broadcast Lock Management
# ==============================


def load_broadcast_locks() -> Dict[str, Any]:
    """Load broadcast locks"""
    return safe_load_json(BROADCAST_LOCKS_FILE)


def save_broadcast_locks(locks: Dict[str, Any]) -> bool:
    """Save broadcast locks"""
    return safe_save_json(BROADCAST_LOCKS_FILE, locks)


def is_account_locked(account: str) -> bool:
    """Check if account is currently locked"""
    locks = load_broadcast_locks()
    return account in locks and locks[account].get("active", False)


def lock_account(account: str, user_id: int) -> bool:
    """Lock account for broadcast"""
    locks = load_broadcast_locks()
    locks[account] = {
        "active": True,
        "started_at": datetime.now().isoformat(),
        "started_by": user_id,
    }
    return save_broadcast_locks(locks)


def unlock_account(account: str) -> bool:
    """Unlock account after broadcast"""
    locks = load_broadcast_locks()
    if account in locks:
        locks[account]["active"] = False
        locks[account]["finished_at"] = datetime.now().isoformat()
    return save_broadcast_locks(locks)


# ==============================
# Account History Management
# ==============================


def get_account_history_file(account: str) -> str:
    """Get path to account history file"""
    return os.path.join("account_histories", f"{account}_sent_history.json")


def get_account_retry_file(account: str) -> str:
    """Get path to account retry file"""
    return os.path.join("account_histories", f"{account}_pending_retry.json")


def get_account_failed_file(account: str) -> str:
    """Get path to account failed users file"""
    return os.path.join("account_histories", f"{account}_failed_users.json")


def clear_account_history(account: str) -> None:
    """Clear account broadcast history"""
    for path in [
        get_account_history_file(account),
        get_account_retry_file(account),
        get_account_failed_file(account),
    ]:
        try:
            safe_save_json(path, {})
        except Exception as e:
            logger.warning(f"Failed to clear {path}: {e}")


# ==============================
# Keyboard Helpers
# ==============================


def get_main_menu() -> ReplyKeyboardMarkup:
    """Get main menu keyboard"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Запустить рассылку")],
            [KeyboardButton(text="➕ Добавить аккаунт")],
            [KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="🔧 Управление аккаунтами")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def get_account_keyboard() -> ReplyKeyboardMarkup:
    """Get account selection keyboard"""
    accounts = get_session_accounts()
    if not accounts:
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True
        )

    names = load_account_names()
    rows = []
    for session_name in accounts:
        display_name = names.get(session_name, session_name)
        rows.append([KeyboardButton(text=f"👤 {display_name}")])

    rows.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def get_mode_keyboard() -> ReplyKeyboardMarkup:
    """Get broadcast mode selection keyboard"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📁 Архивные чаты")],
            [KeyboardButton(text="💬 Обычные чаты")],
            [KeyboardButton(text="🌍 Все чаты")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def get_confirm_keyboard() -> ReplyKeyboardMarkup:
    """Get confirmation keyboard"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="✅ Подтвердить"),
                KeyboardButton(text="✏️ Изменить текст"),
            ],
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отменить")],
        ],
        resize_keyboard=True,
    )


def get_account_management_keyboard() -> ReplyKeyboardMarkup:
    """Get account management keyboard"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗑️ Удалить аккаунт")],
            [KeyboardButton(text="✏️ Переименовать аккаунт")],
            [KeyboardButton(text="🔍 Проверить статус")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def get_account_list_keyboard(action_type: str = "delete") -> ReplyKeyboardMarkup:
    """Get account list keyboard for management actions"""
    accounts = get_session_accounts()
    if not accounts:
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True
        )

    names = load_account_names()
    keyboard = []

    for acc in accounts:
        display = names.get(acc, acc)
        if action_type == "delete" and is_account_locked(acc):
            text = f"🔒 {display} (занят)"
        else:
            text = f"👤 {display}"
        keyboard.append([KeyboardButton(text=text)])

    keyboard.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_delete_confirmation_keyboard() -> ReplyKeyboardMarkup:
    """Get delete confirmation keyboard"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗑️ Да, удалить"), KeyboardButton(text="❌ Отменить")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


# ==============================
# Admin Decorator
# ==============================


def admin_required(func):
    """Decorator to ensure only admins can use certain commands"""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        message = None
        state = None

        # Find message and state in args
        for arg in args:
            if isinstance(arg, types.Message):
                message = arg
            elif isinstance(arg, FSMContext):
                state = arg

        if message is None:
            logger.error("Message argument not found in admin_required decorator")
            return

        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            if state:
                await state.clear()

            await message.answer(
                "❌ Доступ запрещен!\n"
                "Этот бот только для администраторов.\n"
                f"Ваш ID: {user_id}",
                reply_markup=ReplyKeyboardRemove(),
            )
            logger.warning(f"Unauthorized access attempt from user {user_id}")
            return

        # Filter kwargs to match function signature
        sig = inspect.signature(func)
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}

        try:
            return await func(*args, **filtered_kwargs)
        except Exception as e:
            await handle_error(message, e, func.__name__)
            if state:
                await state.clear()

    return wrapper


# ==============================
# Process Cleanup Helper
# ==============================


def cleanup_dead_processes():
    """Clean up finished broadcast processes (non-async, fast operation)"""
    dead_processes = []
    for acc, proc in ACTIVE_BROADCASTS.items():
        if proc.poll() is not None:
            unlock_account(acc)
            dead_processes.append(acc)

    for acc in dead_processes:
        del ACTIVE_BROADCASTS[acc]

    return len(dead_processes)


# ==============================
# Process Management
# ==============================


class ProcessManager:
    """Manage broadcast subprocesses"""

    @staticmethod
    async def start_broadcast(
        account: str, data: Dict[str, Any], user_id: int
    ) -> subprocess.Popen:
        """Start broadcast process for account"""
        try:
            # Validate environment variables for subprocess
            api_id = os.getenv("TELEGRAM_API_ID")
            api_hash = os.getenv("TELEGRAM_API_HASH")

            if not api_id or not api_hash:
                raise ConfigError("TELEGRAM_API_ID or TELEGRAM_API_HASH not configured")

            try:
                int(api_id)  # Validate API ID is numeric
            except ValueError:
                raise ConfigError("TELEGRAM_API_ID must be numeric")

            # Prepare environment
            env = dict(os.environ)
            env.update(
                {
                    "API_ID": api_id,
                    "API_HASH": api_hash,
                    "HISTORY_FILE": get_account_history_file(account),
                    "RETRY_FILE": get_account_retry_file(account),
                    "FAILED_FILE": get_account_failed_file(account),
                    "DEFAULT_MESSAGE": data["text"],
                    "TARGET_MODE": data["mode"],
                    "SESSION_FILE": f"{account}.session",
                    "PHONE_NUMBER": f"+{account}",
                    "ARCHIVE_MODE": "telegram",
                    "SESSION_DIR": SESSION_DIR,
                    "ACCOUNT_FRIENDLY_NAME": get_account_display_name(account),
                    # Пробрасываем токен и чаты для уведомлений
                    "BOT_TOKEN": os.getenv("BOT_TOKEN", ""),
                    "BOT_CHAT_ID": str(
                        user_id
                    ),  # Отправляем уведомления пользователю, запустившему рассылку
                    "ADMIN_PROGRESS_CHAT_ID": str(user_id),  # Прогресс тоже ему
                }
            )

            # Start process
            proc = subprocess.Popen(
                ["python", "messages.py"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="backslashreplace",  # Better error handling - shows \xNN for invalid chars
            )

            ACTIVE_BROADCASTS[account] = proc
            logger.info(f"Broadcast process started: PID={proc.pid}, account={account}")
            return proc

        except Exception as e:
            raise ProcessError(f"Failed to start broadcast process: {e}")

    @staticmethod
    async def wait(
        proc: subprocess.Popen, timeout: int = TIMEOUT_PROCESS
    ) -> Tuple[str, str, int]:
        """Wait for process completion with timeout"""
        try:
            loop = asyncio.get_event_loop()
            stdout, stderr = await asyncio.wait_for(
                loop.run_in_executor(None, proc.communicate), timeout=timeout
            )
            return stdout, stderr, proc.returncode

        except asyncio.TimeoutError:
            # Graceful termination
            try:
                proc.terminate()
                await asyncio.sleep(5)
                if proc.poll() is None:
                    proc.kill()
            except Exception as e:
                logger.error(f"Failed to kill process: {e}")

            raise ProcessError(f"Broadcast process exceeded timeout ({timeout}s)")


# ==============================
# Cleanup Functions
# ==============================


async def cleanup_on_shutdown():
    """Cleanup resources on bot shutdown"""
    logger.info("Начинаю очистку...")

    # Terminate active broadcast processes
    for account, process in ACTIVE_BROADCASTS.items():
        if process.poll() is None:  # Process is still running
            try:
                logger.info(f"Завершаю процесс рассылки для {account}")
                process.terminate()

                # Wait for graceful termination
                await asyncio.sleep(3)

                # Force kill if still running
                if process.poll() is None:
                    process.kill()
                    logger.warning(f"Принудительно завершен процесс для {account}")

            except Exception as e:
                logger.error(f"Ошибка завершения процесса для {account}: {e}")

        # Unlock account
        unlock_account(account)

    ACTIVE_BROADCASTS.clear()
    logger.info("Очистка завершена")


# ==============================
# Command Handlers
# ==============================


@dp.message(Command("start"))
@admin_required
async def cmd_start(message: types.Message, state: FSMContext):
    """Handle /start command"""
    await state.clear()

    # Clean up dead processes periodically
    cleanup_dead_processes()

    user_info = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else f"ID:{message.from_user.id}"
    )
    welcome_message = (
        f"🤖 Добро пожаловать, администратор!\n"
        f"Пользователь: {message.from_user.first_name} ({user_info})\n\n"
        "📋 Функции:\n"
        "• 🚀 Запустить рассылку\n"
        "• ➕ Добавить аккаунт\n"
        "• 📊 Статистика\n"
        "• 🔧 Управление аккаунтами\n\n"
        "Выберите действие:"
    )

    await message.answer(welcome_message, reply_markup=get_main_menu())


@dp.message(F.text == "❌ Отменить")
@admin_required
async def cancel(message: types.Message, state: FSMContext):
    """Handle cancel command - only show if there's an active state"""
    current_state = await state.get_state()

    if current_state:
        await state.clear()
        await message.answer("❌ Операция отменена.", reply_markup=get_main_menu())
    else:
        # If no active state, just show main menu without the "cancelled" message
        await message.answer("🏠 Главное меню:", reply_markup=get_main_menu())


# ==============================
# Broadcast Flow Handlers
# ==============================


@dp.message(F.text == "🚀 Запустить рассылку")
@admin_required
async def start_broadcast(message: types.Message, state: FSMContext):
    """Start broadcast flow"""
    accounts = get_session_accounts()
    if not accounts:
        await message.answer(
            "❌ Аккаунтов нет. Сначала добавьте через «➕ Добавить аккаунт».",
            reply_markup=get_main_menu(),
        )
        return

    await message.answer(
        f"📱 Найдено аккаунтов: {len(accounts)}\nВыберите аккаунт:",
        reply_markup=get_account_keyboard(),
    )
    await state.set_state(BroadcastStates.choosing_account)


@dp.message(BroadcastStates.choosing_account)
@admin_required
async def choose_account(message: types.Message, state: FSMContext):
    """Handle account selection for broadcast"""
    if message.text == "⬅️ Назад":
        await state.clear()
        await message.answer("🏠 Главное меню:", reply_markup=get_main_menu())
        return

    account_name = extract_session_name_from_display(message.text)
    if not account_name:
        await message.answer("❌ Неверный выбор аккаунта. Выберите из списка.")
        return

    session_path = os.path.join(SESSION_DIR, f"{account_name}.session")
    if not os.path.exists(session_path):
        await message.answer("❌ Аккаунт не найден! Выберите из списка.")
        return

    if is_account_locked(account_name):
        locks = load_broadcast_locks()
        lock_info = locks.get(account_name, {})
        start_time = lock_info.get("started_at", "неизвестно")
        await message.answer(
            f"🔒 Этот аккаунт занят (старт: {start_time}). Выберите другой.",
            reply_markup=get_account_keyboard(),
        )
        return

    await state.update_data(account=account_name)
    display_name = get_account_display_name(account_name)
    await message.answer(
        f"✅ Аккаунт: {display_name}\nВыберите режим:", reply_markup=get_mode_keyboard()
    )
    await state.set_state(BroadcastStates.choosing_mode)


@dp.message(BroadcastStates.choosing_mode)
@admin_required
async def choose_mode(message: types.Message, state: FSMContext):
    """Handle broadcast mode selection"""
    if message.text == "⬅️ Назад":
        await message.answer("Выберите аккаунт:", reply_markup=get_account_keyboard())
        await state.set_state(BroadcastStates.choosing_account)
        return

    mode_mapping = {
        "📁 Архивные чаты": "archived",
        "💬 Обычные чаты": "normal",
        "🌍 Все чаты": "all",
    }

    if message.text not in mode_mapping:
        await message.answer("❌ Неверный режим. Используйте кнопки.")
        return

    await state.update_data(mode=mode_mapping[message.text])
    await message.answer(
        "✏️ Введите текст для рассылки (до 4096 символов):",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True
        ),
    )
    await state.set_state(BroadcastStates.entering_text)


@dp.message(BroadcastStates.entering_text)
@admin_required
async def enter_text(message: types.Message, state: FSMContext):
    """Handle message text input"""
    if message.text == "⬅️ Назад":
        await message.answer("Выберите режим:", reply_markup=get_mode_keyboard())
        await state.set_state(BroadcastStates.choosing_mode)
        return

    is_valid, error_msg = validate_message_text(message.text)
    if not is_valid:
        await message.answer(f"❌ {error_msg}")
        return

    await state.update_data(text=message.text)
    data = await state.get_data()

    mode_display = {
        "archived": "📁 Архивные чаты",
        "normal": "💬 Обычные чаты",
        "all": "🌍 Все чаты",
    }

    preview_text = message.text[:200] + ("..." if len(message.text) > 200 else "")

    confirmation_message = (
        "📋 Подтверждение:\n\n"
        f"👤 Аккаунт: {get_account_display_name(data['account'])}\n"
        f"📂 Режим: {mode_display[data['mode']]}\n"
        f"💬 Текст:\n{preview_text}\n"
        f"📏 Длина: {len(message.text)}\n\n"
        "Запустить?"
    )

    await message.answer(confirmation_message, reply_markup=get_confirm_keyboard())
    await state.set_state(BroadcastStates.confirming_broadcast)


@dp.message(BroadcastStates.confirming_broadcast)
@admin_required
async def confirm_broadcast(message: types.Message, state: FSMContext):
    """Handle broadcast confirmation"""
    if message.text == "⬅️ Назад":
        await message.answer(
            "✏️ Введите текст для рассылки:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True
            ),
        )
        await state.set_state(BroadcastStates.entering_text)
        return

    if message.text == "❌ Отменить":
        await cancel(message, state)
        return

    if message.text == "✏️ Изменить текст":
        await message.answer(
            "✏️ Введите новый текст:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True
            ),
        )
        await state.set_state(BroadcastStates.entering_text)
        return

    if message.text != "✅ Подтвердить":
        await message.answer("❌ Используйте кнопки.")
        return

    data = await state.get_data()
    account = data["account"]

    # Double-check account lock status
    if is_account_locked(account):
        await message.answer(
            f"🔒 Аккаунт {get_account_display_name(account)} сейчас занят другой рассылкой.",
            reply_markup=get_main_menu(),
        )
        await state.clear()
        return

    # Lock the account and start broadcast
    if not lock_account(account, message.from_user.id):
        await message.answer(
            "❌ Не удалось заблокировать аккаунт. Попробуйте снова.",
            reply_markup=get_main_menu(),
        )
        await state.clear()
        return

    await state.clear()
    await message.answer(
        f"🚀 Запускаю рассылку...\n"
        f"🔒 Аккаунт {get_account_display_name(account)} заблокирован на время выполнения.",
        reply_markup=get_main_menu(),
    )

    try:
        clear_account_history(account)
        proc = await ProcessManager.start_broadcast(account, data, message.from_user.id)

        # Update statistics
        stats = load_stats()
        stats["broadcasts"].append(
            {
                "datetime": datetime.now().isoformat(),
                "account": account,
                "mode": data["mode"],
                "text_length": len(data["text"]),
                "user_id": message.from_user.id,
                "status": "started",
            }
        )
        save_stats(stats)

        # Start monitoring task
        asyncio.create_task(
            monitor_broadcast_completion(proc, message.from_user.id, data)
        )

    except Exception as e:
        unlock_account(account)
        await message.answer(f"❌ Ошибка запуска: {e}")
        logger.error(f"Broadcast start failed for {account}: {e}")


async def monitor_broadcast_completion(
    proc: subprocess.Popen, user_id: int, broadcast_data: Dict[str, Any]
):
    """Monitor broadcast process completion"""
    account = broadcast_data["account"]

    try:
        stdout, stderr, return_code = await ProcessManager.wait(
            proc, timeout=TIMEOUT_PROCESS
        )

        # Parse results from output - prioritize [RESULT] format
        sent_count = failed_count = total_count = 0
        result_found = False

        for line in stdout.splitlines():
            line_stripped = line.strip()

            # Machine-readable format (priority)
            if line_stripped.startswith("[RESULT]"):
                result_found = True
                # Parse key=value pairs
                for k, v in re.findall(r"(\w+)=(\d+)", line_stripped):
                    if k == "sent":
                        sent_count = int(v)
                    elif k == "failed":
                        failed_count = int(v)
                    elif k == "skipped":
                        # Include skipped in the parsing
                        pass  # Can add skip_count if needed
                    elif k == "total":
                        total_count = int(v)
                break  # Stop after finding [RESULT] line

        # Fallback: use heuristics only if [RESULT] not found
        if not result_found:
            logger.warning(f"[RESULT] format not found for {account}, using heuristics")
            for line in stdout.splitlines():
                line_stripped = line.strip()

                if (
                    "[OK] Sent" in line_stripped
                    or "Message sent successfully" in line_stripped
                ):
                    sent_count += 1
                elif "[SKIP]" in line_stripped or "[FAIL]" in line_stripped:
                    failed_count += 1

        mode_display = {
            "archived": "📁 Архивные",
            "normal": "💬 Обычные",
            "all": "🌍 Все",
        }

        if return_code == 0:
            # Successful completion
            report_message = (
                "✅ Рассылка завершена!\n\n"
                f"📊 Отправлено: {sent_count}\n"
                f"❌ Не отправлено: {failed_count}\n"
                f"👥 Всего: {total_count if total_count else sent_count + failed_count}\n\n"
                f"👤 Аккаунт: {get_account_display_name(account)}\n"
                f"📂 Режим: {mode_display.get(broadcast_data['mode'], broadcast_data['mode'])}\n"
                f"🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
                "🔓 Аккаунт разблокирован."
            )

            # Update successful broadcast stats
            stats = load_stats()
            stats["total_messages"] = stats.get("total_messages", 0) + sent_count
            stats["successful_broadcasts"] = stats.get("successful_broadcasts", 0) + 1
            save_stats(stats)

        else:
            # Failed completion
            error_tail = (stderr or "Нет деталей ошибки")[-500:]  # Last 500 chars
            report_message = (
                "⚠️ Рассылка завершена с ошибкой.\n\n"
                f"📊 Отправлено: {sent_count}\n"
                f"❌ Не отправлено: {failed_count}\n\n"
                f"Детали ошибки:\n{error_tail}\n\n"
                "🔓 Аккаунт разблокирован."
            )

        await bot.send_message(user_id, report_message)

    except Exception as e:
        error_message = f"❌ Ошибка ожидания процесса: {e}\n🔓 Аккаунт разблокирован."
        await bot.send_message(user_id, error_message)
        logger.error(f"Broadcast monitoring failed for {account}: {e}")

    finally:
        unlock_account(account)
        if account in ACTIVE_BROADCASTS:
            del ACTIVE_BROADCASTS[account]


# ==============================
# Statistics Handler
# ==============================


@dp.message(F.text == "📊 Статистика")
@admin_required
async def show_statistics(message: types.Message):
    """Display broadcast statistics"""
    stats = load_stats()

    if not stats["broadcasts"]:
        await message.answer(
            "📊 Статистика пуста. Рассылок ещё не было.", reply_markup=get_main_menu()
        )
        return

    recent_broadcasts = stats["broadcasts"][-5:]  # Last 5 broadcasts

    stats_message = (
        "📊 Статистика рассылок:\n\n"
        f"📈 Всего рассылок: {len(stats['broadcasts'])}\n"
        f"✅ Успешных: {stats.get('successful_broadcasts', 0)}\n"
        f"💬 Всего сообщений: {stats.get('total_messages', 0)}\n\n"
        "📅 Последние рассылки:\n"
    )

    for i, broadcast in enumerate(reversed(recent_broadcasts), 1):
        try:
            dt = datetime.fromisoformat(broadcast["datetime"])
            formatted_time = dt.strftime("%d.%m %H:%M")
        except (ValueError, KeyError):
            formatted_time = "неизвестно"

        mode_icon = {"archived": "📁", "normal": "💬", "all": "🌍"}.get(
            broadcast.get("mode", ""), "❓"
        )
        account_name = get_account_display_name(broadcast.get("account", "неизвестно"))

        stats_message += f"{i}. {formatted_time} | {mode_icon} {account_name}\n"

    await message.answer(stats_message, reply_markup=get_main_menu())


# ==============================
# Account Management Handlers
# # ==============================


@dp.message(F.text == "🔧 Управление аккаунтами")
@admin_required
async def account_management_menu(message: types.Message, state: FSMContext):
    """Show account management menu"""
    accounts = get_session_accounts()
    if not accounts:
        await message.answer(
            "❌ Аккаунтов нет. Сначала добавьте через «➕ Добавить аккаунт».",
            reply_markup=get_main_menu(),
        )
        return

    await message.answer(
        f"🔧 Управление аккаунтами\n\n"
        f"📱 Найдено аккаунтов: {len(accounts)}\n"
        f"Выберите действие:",
        reply_markup=get_account_management_keyboard(),
    )
    await state.set_state(ManageAccountStates.choosing_action)


@dp.message(ManageAccountStates.choosing_action)
@admin_required
async def handle_management_action(message: types.Message, state: FSMContext):
    """Handle account management action selection"""
    if message.text == "⬅️ Назад":
        await state.clear()
        await message.answer("🏠 Главное меню:", reply_markup=get_main_menu())
        return

    action_handlers = {
        "🗑️ Удалить аккаунт": handle_delete_account_selection,
        "✏️ Переименовать аккаунт": handle_rename_account_selection,
        "🔍 Проверить статус": handle_status_check_selection,
    }

    handler = action_handlers.get(message.text)
    if handler:
        await handler(message, state)
    else:
        await message.answer("❌ Используйте кнопки меню.")


async def handle_delete_account_selection(message: types.Message, state: FSMContext):
    """Handle account deletion selection"""
    accounts = get_session_accounts()
    if not accounts:
        await message.answer(
            "❌ Нет аккаунтов для удаления.",
            reply_markup=get_account_management_keyboard(),
        )
        return

    await message.answer(
        "🗑️ Выберите аккаунт для удаления:\n\n"
        "⚠️ Внимание: Это действие необратимо!\n"
        "🔒 Заблокированные аккаунты удалить нельзя.",
        reply_markup=get_account_list_keyboard("delete"),
    )
    await state.update_data(action="delete")
    await state.set_state(ManageAccountStates.selecting_account)


async def handle_rename_account_selection(message: types.Message, state: FSMContext):
    """Handle account rename selection"""
    accounts = get_session_accounts()
    if not accounts:
        await message.answer(
            "❌ Нет аккаунтов для переименования.",
            reply_markup=get_account_management_keyboard(),
        )
        return

    await message.answer(
        "✏️ Выберите аккаунт для переименования:",
        reply_markup=get_account_list_keyboard("rename"),
    )
    await state.update_data(action="rename")
    await state.set_state(ManageAccountStates.selecting_account)


async def handle_status_check_selection(message: types.Message, state: FSMContext):
    """Handle account status check selection"""
    accounts = get_session_accounts()
    if not accounts:
        await message.answer(
            "❌ Нет аккаунтов для проверки.",
            reply_markup=get_account_management_keyboard(),
        )
        return

    await message.answer(
        "🔍 Выберите аккаунт для проверки статуса:",
        reply_markup=get_account_list_keyboard("status"),
    )
    await state.update_data(action="status")
    await state.set_state(ManageAccountStates.selecting_account)


@dp.message(ManageAccountStates.selecting_account)
@admin_required
async def handle_account_selection(message: types.Message, state: FSMContext):
    """Handle account selection for management actions"""
    if message.text == "⬅️ Назад":
        await message.answer(
            "🔧 Управление аккаунтами:", reply_markup=get_account_management_keyboard()
        )
        await state.set_state(ManageAccountStates.choosing_action)
        return

    data = await state.get_data()
    action = data.get("action")

    if not action:
        await state.clear()
        await message.answer("❌ Ошибка сессии.", reply_markup=get_main_menu())
        return

    account_name = extract_session_name_from_display(message.text)
    if not account_name:
        await message.answer("❌ Неверный выбор аккаунта. Используйте кнопки.")
        return

    # Check if account exists
    session_path = os.path.join(SESSION_DIR, f"{account_name}.session")
    if not os.path.exists(session_path):
        await message.answer("❌ Аккаунт не найден!")
        return

    await state.update_data(selected_account=account_name)

    if action == "delete":
        await handle_delete_confirmation(message, state)
    elif action == "rename":
        await handle_rename_input(message, state)
    elif action == "status":
        await handle_status_check(message, state)


async def handle_delete_confirmation(message: types.Message, state: FSMContext):
    """Show delete confirmation"""
    data = await state.get_data()
    account_name = data.get("selected_account")

    # Check if account is locked
    if is_account_locked(account_name):
        await message.answer(
            f"🔒 Аккаунт {get_account_display_name(account_name)} заблокирован и не может быть удален.\n"
            "Дождитесь завершения рассылки.",
            reply_markup=get_account_list_keyboard("delete"),
        )
        return

    display_name = get_account_display_name(account_name)
    await message.answer(
        f"⚠️ Подтверждение удаления\n\n"
        f"👤 Аккаунт: {display_name}\n"
        f"📁 Сессия: {account_name}.session\n\n"
        f"❗ Это действие необратимо!\n"
        f"Все данные аккаунта будут удалены.\n\n"
        f"Продолжить?",
        reply_markup=get_delete_confirmation_keyboard(),
    )
    await state.set_state(ManageAccountStates.confirming_delete)


@dp.message(ManageAccountStates.confirming_delete)
@admin_required
async def handle_delete_confirmation_response(
    message: types.Message, state: FSMContext
):
    """Handle delete confirmation response"""
    if message.text == "⬅️ Назад":
        data = await state.get_data()
        await message.answer(
            "🗑️ Выберите аккаунт для удаления:",
            reply_markup=get_account_list_keyboard("delete"),
        )
        await state.set_state(ManageAccountStates.selecting_account)
        return

    if message.text == "❌ Отменить":
        await cancel(message, state)
        return

    if message.text != "🗑️ Да, удалить":
        await message.answer("❌ Используйте кнопки.")
        return

    data = await state.get_data()
    account_name = data.get("selected_account")

    if not account_name:
        await message.answer("❌ Ошибка сессии.", reply_markup=get_main_menu())
        await state.clear()
        return

    # Double-check lock status
    if is_account_locked(account_name):
        await message.answer(
            f"🔒 Аккаунт {get_account_display_name(account_name)} заблокирован.\n"
            "Удаление отменено.",
            reply_markup=get_main_menu(),
        )
        await state.clear()
        return

    try:
        display_name = get_account_display_name(account_name)
        session_path = os.path.join(SESSION_DIR, f"{account_name}.session")

        # Remove session file
        if os.path.exists(session_path):
            os.remove(session_path)

        # Remove from account names
        names = load_account_names()
        if account_name in names:
            del names[account_name]
            save_account_names(names)

        # Clear history files
        clear_account_history(account_name)

        await message.answer(
            f"✅ Аккаунт удален!\n\n"
            f"👤 {display_name}\n"
            f"📁 {account_name}.session\n\n"
            f"🗑️ Все связанные данные очищены.",
            reply_markup=get_main_menu(),
        )

        logger.info(f"Account {account_name} deleted by user {message.from_user.id}")
        await state.clear()

    except Exception as e:
        await message.answer(
            f"❌ Ошибка удаления аккаунта: {e}", reply_markup=get_main_menu()
        )
        logger.error(f"Failed to delete account {account_name}: {e}")
        await state.clear()


async def handle_rename_input(message: types.Message, state: FSMContext):
    """Handle rename input"""
    data = await state.get_data()
    account_name = data.get("selected_account")
    current_name = get_account_display_name(account_name)

    await message.answer(
        f"✏️ Переименование аккаунта\n\n"
        f"📁 Сессия: {account_name}.session\n"
        f"👤 Текущее имя: {current_name}\n\n"
        f"Введите новое отображаемое имя:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True
        ),
    )
    await state.set_state(ManageAccountStates.editing_name)


@dp.message(ManageAccountStates.editing_name)
@admin_required
async def handle_rename_save(message: types.Message, state: FSMContext):
    """Handle rename save"""
    if message.text == "⬅️ Назад":
        await message.answer(
            "✏️ Выберите аккаунт для переименования:",
            reply_markup=get_account_list_keyboard("rename"),
        )
        await state.set_state(ManageAccountStates.selecting_account)
        return

    is_valid, error_msg = validate_account_name(message.text)
    if not is_valid:
        await message.answer(f"❌ {error_msg}")
        return

    data = await state.get_data()
    account_name = data.get("selected_account")
    new_name = message.text.strip()
    old_name = get_account_display_name(account_name)

    try:
        names = load_account_names()
        names[account_name] = new_name

        if save_account_names(names):
            await message.answer(
                f"✅ Аккаунт переименован!\n\n"
                f"📁 Сессия: {account_name}.session\n"
                f"👤 Старое имя: {old_name}\n"
                f"👤 Новое имя: {new_name}",
                reply_markup=get_main_menu(),
            )

            logger.info(
                f"Account {account_name} renamed from '{old_name}' to '{new_name}' by user {message.from_user.id}"
            )
        else:
            await message.answer(
                "❌ Не удалось сохранить новое имя.", reply_markup=get_main_menu()
            )

        await state.clear()

    except Exception as e:
        await message.answer(
            f"❌ Ошибка переименования: {e}", reply_markup=get_main_menu()
        )
        logger.error(f"Failed to rename account {account_name}: {e}")
        await state.clear()


async def handle_status_check(message: types.Message, state: FSMContext):
    """Handle account status check"""
    data = await state.get_data()
    account_name = data.get("selected_account")
    display_name = get_account_display_name(account_name)

    await message.answer(f"🔍 Проверяю статус аккаунта {display_name}...")

    try:
        api_id, api_hash = get_environment_config()
        session_path = os.path.join(SESSION_DIR, account_name)

        async with telethon_client(session_path, api_id, api_hash) as client:
            try:
                if await client.is_user_authorized():
                    me = await client.get_me()
                    if me:
                        phone = getattr(me, "phone", "Не указан")
                        username = f"@{me.username}" if me.username else "Нет username"

                        # Check if account is locked
                        is_locked = is_account_locked(account_name)
                        lock_status = "🔒 Заблокирован" if is_locked else "🔓 Свободен"

                        # Get session file info
                        session_file_path = os.path.join(
                            SESSION_DIR, f"{account_name}.session"
                        )
                        if os.path.exists(session_file_path):
                            stat_info = os.stat(session_file_path)
                            file_size = stat_info.st_size
                            modified_time = datetime.fromtimestamp(stat_info.st_mtime)
                            file_info = f"📁 Размер: {file_size} байт\n📅 Изменен: {modified_time.strftime('%d.%m.%Y %H:%M')}"
                        else:
                            file_info = "❌ Файл сессии не найден"

                        status_message = (
                            f"✅ Аккаунт активен\n\n"
                            f"👤 Имя: {me.first_name or ''} {me.last_name or ''}".strip()
                            + "\n"
                            f"📱 Телефон: +{phone}\n"
                            f"🔗 Username: {username}\n"
                            f"🆔 ID: {me.id}\n"
                            f"🔐 Статус: {lock_status}\n\n"
                            f"📋 Отображаемое имя: {display_name}\n"
                            f"📁 Сессия: {account_name}.session\n"
                            f"{file_info}"
                        )
                    else:
                        status_message = (
                            "❌ Не удалось получить информацию о пользователе"
                        )
                else:
                    status_message = (
                        f"❌ Аккаунт не авторизован\n\n"
                        f"📋 Отображаемое имя: {display_name}\n"
                        f"📁 Сессия: {account_name}.session\n\n"
                        f"Требуется повторная авторизация."
                    )

            except Exception as e:
                status_message = (
                    f"❌ Ошибка подключения\n\n"
                    f"📋 Отображаемое имя: {display_name}\n"
                    f"📁 Сессия: {account_name}.session\n\n"
                    f"Детали: {str(e)[:200]}"
                )

    except Exception as e:
        status_message = f"❌ Критическая ошибка проверки: {e}"
        logger.error(f"Status check failed for {account_name}: {e}")

    await message.answer(status_message, reply_markup=get_main_menu())
    await state.clear()


# ==============================
# Add Account Handlers
# ==============================


@dp.message(F.text == "➕ Добавить аккаунт")
@admin_required
async def add_account_start(message: types.Message, state: FSMContext):
    """Start account addition process"""
    # Verify API credentials are configured
    try:
        api_id, api_hash = get_environment_config()
    except ConfigError as e:
        await message.answer(
            f"❌ Ошибка конфигурации: {e}\n"
            "Настройте TELEGRAM_API_ID и TELEGRAM_API_HASH в .env файле.\n"
            "Получить можно на https://my.telegram.org",
            reply_markup=get_main_menu(),
        )
        return

    instructions = (
        "➕ Добавление аккаунта\n\n"
        "📋 Процесс:\n"
        "1) Введите номер телефона в формате +1234567890\n"
        "2) Введите код подтверждения из Telegram\n"
        "3) При необходимости - пароль 2FA\n\n"
        "📱 Введите номер телефона:"
    )

    await message.answer(
        instructions,
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отменить")]], resize_keyboard=True
        ),
    )
    await state.set_state(AddAccountStates.entering_phone)


@dp.message(AddAccountStates.entering_phone)
@admin_required
async def add_account_phone(message: types.Message, state: FSMContext):
    """Handle phone number input"""
    if message.text == "❌ Отменить":
        await cancel(message, state)
        return

    is_valid, error_msg = validate_phone(message.text)
    if not is_valid:
        await message.answer(f"❌ {error_msg}")
        return

    phone = message.text.strip()
    session_name = re.sub(r"[^\d]", "", phone[1:])  # Extract digits only
    session_path = os.path.join(SESSION_DIR, f"{session_name}.session")

    if os.path.exists(session_path):
        await message.answer(
            f"⚠️ Сессия уже существует: {session_name}.session\nПерезаписать?",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="✅ Да, перезаписать")],
                    [KeyboardButton(text="❌ Нет, отменить")],
                ],
                resize_keyboard=True,
            ),
        )
        await state.update_data(
            phone=phone, session_name=session_name, overwrite_pending=True
        )
        return

    await state.update_data(phone=phone, session_name=session_name)
    await start_telethon_auth(message, state)


async def start_telethon_auth(message: types.Message, state: FSMContext):
    """Start Telethon authentication process"""
    data = await state.get_data()
    phone = data["phone"]
    session_name = data["session_name"]

    try:
        api_id, api_hash = get_environment_config()
    except ConfigError as e:
        await message.answer(
            f"❌ Ошибка конфигурации: {e}", reply_markup=get_main_menu()
        )
        await state.clear()
        return

    session_path = os.path.join(SESSION_DIR, session_name)

    await message.answer(f"📡 Подключаюсь к Telegram API...\n📱 {phone}")

    try:
        async with telethon_client(session_path, api_id, api_hash) as client:
            from telethon.errors import PhoneNumberInvalidError, FloodWaitError

            # Check if already authorized
            if await client.is_user_authorized():
                me = await client.get_me()
                if me:
                    name_parts = [me.first_name or "", me.last_name or ""]
                    display_name = (
                        " ".join(part for part in name_parts if part).strip() or phone
                    )

                    # Save display name
                    names = load_account_names()
                    names[session_name] = display_name
                    save_account_names(names)

                    await message.answer(
                        f"✅ Аккаунт уже авторизован\n👤 {display_name}",
                        reply_markup=get_main_menu(),
                    )
                    await state.clear()
                    return

            # Send verification code
            logger.info(f"Requesting verification code for {phone}")
            sent_code = await asyncio.wait_for(
                client.send_code_request(phone), timeout=TIMEOUT_NETWORK
            )

            logger.info(f"Code request successful. Type: {sent_code.type}")

            await state.update_data(phone_code_hash=sent_code.phone_code_hash)

            # Определяем способ отправки кода
            code_type = type(sent_code.type).__name__
            if "App" in code_type:
                code_info = (
                    "📱 Код отправлен в приложение Telegram\n\n"
                    "⚠️ Проверьте приложение Telegram на телефоне!\n"
                    "Код должен прийти в чат с Telegram.\n\n"
                    "Если код не пришел, нажмите кнопку ниже для отправки по SMS."
                )
                keyboard = [
                    [KeyboardButton(text="📨 Отправить код по SMS")],
                    [KeyboardButton(text="❌ Отменить")],
                ]
            elif "Sms" in code_type:
                code_info = "📨 Код отправлен по SMS"
                keyboard = [[KeyboardButton(text="❌ Отменить")]]
            elif "Call" in code_type:
                code_info = "📞 Код будет продиктован по телефону"
                keyboard = [[KeyboardButton(text="❌ Отменить")]]
            else:
                code_info = f"📨 Код отправлен на {phone}"
                keyboard = [[KeyboardButton(text="❌ Отменить")]]

            await message.answer(
                f"{code_info}\n\nВведите код (только цифры):",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=keyboard,
                    resize_keyboard=True,
                ),
            )
            await state.set_state(AddAccountStates.entering_code)

    except PhoneNumberInvalidError:
        cleanup_failed_session(session_name)
        await message.answer(
            "❌ Неверный номер телефона.", reply_markup=get_main_menu()
        )
        await state.clear()
        logger.error(f"Invalid phone number: {phone}")
    except FloodWaitError as e:
        cleanup_failed_session(session_name)
        wait_seconds = getattr(e, "seconds", 60)
        await message.answer(
            f"⏰ Слишком много попыток. Подождите {wait_seconds} секунд.",
            reply_markup=get_main_menu(),
        )
        await state.clear()
        logger.warning(f"FloodWait for {wait_seconds}s on phone {phone}")
    except asyncio.TimeoutError:
        cleanup_failed_session(session_name)
        await message.answer(
            "⏰ Таймаут подключения к Telegram API.\nПопробуйте позже.",
            reply_markup=get_main_menu(),
        )
        await state.clear()
        logger.error(f"Timeout requesting code for {phone}")
    except Exception as e:
        cleanup_failed_session(session_name)
        error_msg = str(e)
        await message.answer(
            f"❌ Ошибка при запросе кода:\n{error_msg[:200]}",
            reply_markup=get_main_menu(),
        )
        await state.clear()
        logger.error(f"Telethon auth error for {phone}: {e}", exc_info=True)


@dp.message(AddAccountStates.entering_code)
@admin_required
async def add_account_code(message: types.Message, state: FSMContext):
    """Handle verification code input"""
    if message.text == "❌ Отменить":
        # Clean up session file on cancel
        data = await state.get_data()
        session_name = data.get("session_name")
        if session_name:
            cleanup_failed_session(session_name)
        await cancel(message, state)
        return

    # Handle SMS request
    if message.text == "📨 Отправить код по SMS":
        data = await state.get_data()
        phone = data.get("phone")
        session_name = data.get("session_name")

        if not phone or not session_name:
            await message.answer(
                "❌ Ошибка сессии. Начните заново.", reply_markup=get_main_menu()
            )
            await state.clear()
            return

        try:
            api_id, api_hash = get_environment_config()
            session_path = os.path.join(SESSION_DIR, session_name)

            await message.answer("📨 Запрашиваю отправку кода по SMS...")

            async with telethon_client(session_path, api_id, api_hash) as client:
                # Request code via SMS
                sent_code = await client.send_code_request(phone, force_sms=True)
                await state.update_data(phone_code_hash=sent_code.phone_code_hash)

                await message.answer(
                    "✅ Код отправлен по SMS!\n\nВведите код (только цифры):",
                    reply_markup=ReplyKeyboardMarkup(
                        keyboard=[[KeyboardButton(text="❌ Отменить")]],
                        resize_keyboard=True,
                    ),
                )
                logger.info(f"SMS code requested for {phone}")
                return

        except Exception as e:
            await message.answer(f"❌ Ошибка запроса SMS: {e}")
            logger.error(f"SMS request failed for {phone}: {e}")
            return

    is_valid, clean_code = validate_code(message.text)
    if not is_valid:
        await message.answer(
            f"❌ {clean_code}"
        )  # Error message is in second return value
        return

    data = await state.get_data()
    phone = data["phone"]
    session_name = data["session_name"]
    phone_code_hash = data.get("phone_code_hash")

    if not phone_code_hash:
        await message.answer("❌ Сессия истекла. Начните заново.")
        await state.clear()
        return

    try:
        api_id, api_hash = get_environment_config()
        session_path = os.path.join(SESSION_DIR, session_name)

        async with telethon_client(session_path, api_id, api_hash) as client:
            from telethon.errors import (
                SessionPasswordNeededError,
                PhoneCodeInvalidError,
            )

            try:
                await client.sign_in(
                    phone=phone, code=clean_code, phone_code_hash=phone_code_hash
                )

                # Get user info and save
                me = await client.get_me()
                if me:
                    name_parts = [me.first_name or "", me.last_name or ""]
                    display_name = (
                        " ".join(part for part in name_parts if part).strip() or phone
                    )

                    # Save display name
                    names = load_account_names()
                    names[session_name] = display_name
                    save_account_names(names)

                    await message.answer(
                        f"✅ Аккаунт добавлен!\n👤 {display_name}\n💾 Сессия сохранена.",
                        reply_markup=get_main_menu(),
                    )
                    logger.info(
                        f"Account {session_name} added successfully by user {message.from_user.id}"
                    )
                    await state.clear()
                else:
                    await message.answer(
                        "❌ Не удалось получить информацию о пользователе."
                    )

            except SessionPasswordNeededError:
                await message.answer(
                    "🔒 Включена двухфакторная авторизация. Введите пароль:",
                    reply_markup=ReplyKeyboardMarkup(
                        keyboard=[[KeyboardButton(text="❌ Отменить")]],
                        resize_keyboard=True,
                    ),
                )
                await state.set_state(AddAccountStates.entering_password)

            except PhoneCodeInvalidError:
                await message.answer("❌ Неверный код подтверждения. Попробуйте снова.")
                # Don't cleanup session yet - allow retry

    except Exception as e:
        # Clean up session on error
        cleanup_failed_session(session_name)
        await message.answer(f"❌ Ошибка входа: {e}")
        logger.error(f"Code verification failed for {session_name}: {e}")
        await state.clear()


@dp.message(AddAccountStates.entering_password)
@admin_required
async def add_account_password(message: types.Message, state: FSMContext):
    """Handle 2FA password input"""
    if message.text == "❌ Отменить":
        # Clean up session file on cancel
        data = await state.get_data()
        session_name = data.get("session_name")
        if session_name:
            cleanup_failed_session(session_name)
        await cancel(message, state)
        return

    if not message.text or len(message.text.strip()) == 0:
        await message.answer("❌ Пароль не может быть пустым.")
        return

    data = await state.get_data()
    phone = data["phone"]
    session_name = data["session_name"]
    phone_code_hash = data.get("phone_code_hash")
    password = message.text

    try:
        api_id, api_hash = get_environment_config()
        session_path = os.path.join(SESSION_DIR, session_name)

        async with telethon_client(session_path, api_id, api_hash) as client:
            from telethon.errors import PasswordHashInvalidError

            try:
                await client.sign_in(
                    phone=phone, password=password, phone_code_hash=phone_code_hash
                )

                # Get user info and save
                me = await client.get_me()
                if me:
                    name_parts = [me.first_name or "", me.last_name or ""]
                    display_name = (
                        " ".join(part for part in name_parts if part).strip() or phone
                    )

                    # Save display name
                    names = load_account_names()
                    names[session_name] = display_name
                    save_account_names(names)

                    await message.answer(
                        f"✅ Аккаунт добавлен!\n👤 {display_name}",
                        reply_markup=get_main_menu(),
                    )
                    logger.info(
                        f"Account {session_name} added with 2FA by user {message.from_user.id}"
                    )
                    await state.clear()
                else:
                    await message.answer(
                        "❌ Не удалось получить информацию о пользователе."
                    )

            except PasswordHashInvalidError:
                await message.answer("❌ Неверный пароль. Попробуйте снова.")
                # Don't cleanup session yet - allow retry

    except Exception as e:
        # Clean up session on error
        cleanup_failed_session(session_name)
        await message.answer(f"❌ Ошибка 2FA: {e}")
        logger.error(f"2FA authentication failed for {session_name}: {e}")
        await state.clear()


# Handle overwrite confirmation for existing sessions
@dp.message(
    AddAccountStates.entering_phone,
    F.text.in_(["✅ Да, перезаписать", "❌ Нет, отменить"]),
)
@admin_required
async def handle_overwrite_confirmation(message: types.Message, state: FSMContext):
    """Handle session overwrite confirmation"""
    data = await state.get_data()
    if not data.get("overwrite_pending"):
        return

    if message.text == "❌ Нет, отменить":
        await cancel(message, state)
        return

    if message.text == "✅ Да, перезаписать":
        phone = data.get("phone")
        session_name = data.get("session_name")

        if not phone or not session_name:
            await message.answer(
                "❌ Ошибка сессии. Начните заново.", reply_markup=get_main_menu()
            )
            await state.clear()
            return

        # Delete old session file
        session_path = os.path.join(SESSION_DIR, f"{session_name}.session")
        try:
            if os.path.exists(session_path):
                os.remove(session_path)
                logger.info(f"Removed old session file: {session_path}")
        except Exception as e:
            logger.error(f"Failed to remove old session: {e}")

        # Remove overwrite flag and proceed with auth
        await state.update_data(overwrite_pending=False)
        await start_telethon_auth(message, state)


# Global error handler for unhandled messages
@dp.message()
@admin_required
async def handle_unknown_message(message: types.Message, state: FSMContext):
    """Handle unknown or unmatched messages"""
    current_state = await state.get_state()

    if current_state:
        await message.answer(
            "❌ Нераспознанный ввод. Используйте кнопки или следуйте инструкциям."
        )
    else:
        await message.answer(
            "❌ Неизвестная команда. Используйте главное меню.",
            reply_markup=get_main_menu(),
        )


# ==============================
# Main Application Entry Point
# ==============================


async def main():
    """Main application entry point"""
    logger.info("Запуск Telegram Broadcast Bot...")

    try:
        # Validate configuration before starting
        if not API_TOKEN:
            raise ConfigError("BOT_TOKEN не настроен")

        if not ADMIN_IDS:
            raise ConfigError("ADMIN_IDS не настроены")

        logger.info(f"Бот настроен с {len(ADMIN_IDS)} администратором(ами)")

        # Start polling
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Не удалось запустить бот: {e}", exc_info=True)
        raise
    finally:
        await cleanup_on_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен пользователем")
    except Exception as e:
        logger.error(f"Приложение упало: {e}", exc_info=True)
        raise SystemExit(1)
