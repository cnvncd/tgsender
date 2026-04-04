#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Broadcast Engine - Message Sending Component
Handles the actual message broadcasting using Telethon
"""

import os
import time
import json
import logging
import asyncio
import urllib.parse
import urllib.request
import re
from typing import Dict, List, Tuple, Optional
from contextlib import asynccontextmanager

from telethon import TelegramClient, errors
from telethon.tl.types import InputPeerUser

# Configuration constants
SKIP_SERVICE_IDS = {777000, 1087968824}  # Telegram notifications, Group Anonymous Bot
MAX_RETRY_ATTEMPTS = 3
DEFAULT_REQUEST_TIMEOUT = 15
BOT_API_RATE_LIMIT_DELAY = 0.05  # 50ms between bot API calls to avoid rate limits


def str_to_bool(val: str, default: bool = False) -> bool:
    """Convert string to boolean safely"""
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}


class ConfigurationError(Exception):
    """Raised when configuration is invalid"""

    pass


class NetworkError(Exception):
    """Raised when network operations fail"""

    pass


class BroadcastConfig:
    """Configuration management for broadcast operations"""

    def __init__(self):
        """Initialize configuration from environment variables with validation"""
        # Core authentication - required
        try:
            self.API_ID = int(os.getenv("API_ID", "0"))
        except (ValueError, TypeError):
            raise ConfigurationError("API_ID must be a valid integer")

        self.API_HASH = os.getenv("API_HASH", "").strip()
        self.SESSION_FILE = os.getenv("SESSION_FILE", "").strip()

        # Message and targeting
        self.DEFAULT_MESSAGE = os.getenv("DEFAULT_MESSAGE", "").strip()
        self.TARGET_MODE = os.getenv(
            "TARGET_MODE", "all"
        ).lower()  # archived|normal|all
        self.ARCHIVE_MODE = os.getenv("ARCHIVE_MODE", "telegram").lower()

        # File paths
        self.HISTORY_FILE = os.getenv("HISTORY_FILE", "history.json")
        self.RETRY_FILE = os.getenv("RETRY_FILE", "retry.json")
        self.FAILED_FILE = os.getenv("FAILED_FILE", "failed.json")

        # Timing configuration with validation
        try:
            self.MESSAGE_DELAY = max(0.1, float(os.getenv("MESSAGE_DELAY", "0.3")))
            self.MAX_MESSAGE_DELAY = max(
                1.0, float(os.getenv("MAX_MESSAGE_DELAY", "30"))
            )
        except (ValueError, TypeError):
            raise ConfigurationError(
                "MESSAGE_DELAY and MAX_MESSAGE_DELAY must be valid numbers"
            )

        # Bot reporting
        self.BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
        self.BOT_CHAT_ID = os.getenv("BOT_CHAT_ID", "").strip()
        self.ADMIN_PROGRESS_CHAT_ID = os.getenv("ADMIN_PROGRESS_CHAT_ID", "").strip()
        self.ACCOUNT_FRIENDLY_NAME = os.getenv("ACCOUNT_FRIENDLY_NAME", "").strip()

        # Session directory
        self.SESSION_DIR = os.getenv("SESSION_DIR", "").strip()

        # Validate required fields
        missing_fields = []
        if self.API_ID == 0:
            missing_fields.append("API_ID")
        if not self.API_HASH:
            missing_fields.append("API_HASH")
        if not self.SESSION_FILE:
            missing_fields.append("SESSION_FILE")

        if missing_fields:
            raise ConfigurationError(
                f"Missing required environment variables: {', '.join(missing_fields)}"
            )

        # Validate target mode
        if self.TARGET_MODE not in {"archived", "normal", "all"}:
            raise ConfigurationError(
                f"Invalid TARGET_MODE: {self.TARGET_MODE}. Must be 'archived', 'normal', or 'all'"
            )

    def get_session_path(self) -> str:
        """Get full session file path"""
        if self.SESSION_DIR:
            return os.path.join(self.SESSION_DIR, self.SESSION_FILE)
        return self.SESSION_FILE


def safe_load_json(file_path: str) -> Dict:
    """Safely load JSON file with error handling"""
    if not file_path or not os.path.exists(file_path):
        return {}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        logging.error(f"Error loading JSON from {file_path}: {e}")
        return {}


def safe_save_json(file_path: str, data: Dict) -> bool:
    """Safely save JSON file with atomic write"""
    if not file_path:
        return False

    temp_path = f"{file_path}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Atomic replace
        if os.path.exists(file_path):
            os.replace(temp_path, file_path)
        else:
            os.rename(temp_path, file_path)
        return True

    except (OSError, TypeError, ValueError) as e:
        logging.error(f"Error saving JSON to {file_path}: {e}")
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        return False


def sanitize_html(text: str) -> str:
    """Sanitize text for HTML output to prevent injection"""
    if not text:
        return ""

    # Replace HTML special characters
    replacements = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#x27;",
        "/": "&#x2F;",
    }

    for char, entity in replacements.items():
        text = text.replace(char, entity)

    return text


def send_bot_notification(
    bot_token: str, chat_id: str, text: str, disable_notification: bool = False
) -> bool:
    """Send notification via Telegram Bot API using urllib"""
    if not bot_token or not chat_id or not text:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    # Sanitize text to prevent HTML injection
    text = sanitize_html(text)

    payload = {
        "chat_id": chat_id,
        "text": text[:4000],  # Telegram message limit with buffer
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
        "disable_notification": str(disable_notification).lower(),
    }

    data = urllib.parse.urlencode(payload).encode("utf-8")

    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            # Rate limiting to avoid hitting Telegram Bot API limits
            if attempt > 0:
                time.sleep(BOT_API_RATE_LIMIT_DELAY)

            request = urllib.request.Request(url, data=data, method="POST")
            request.add_header("Content-Type", "application/x-www-form-urlencoded")

            with urllib.request.urlopen(
                request, timeout=DEFAULT_REQUEST_TIMEOUT
            ) as response:
                if response.status == 200:
                    return True
                else:
                    logging.warning(f"Bot API returned status {response.status}")

        except urllib.error.HTTPError as e:
            logging.warning(f"Bot API HTTP error (attempt {attempt + 1}): {e}")
        except urllib.error.URLError as e:
            logging.warning(f"Bot API URL error (attempt {attempt + 1}): {e}")
        except Exception as e:
            logging.warning(f"Bot API unexpected error (attempt {attempt + 1}): {e}")

        if attempt < MAX_RETRY_ATTEMPTS - 1:
            time.sleep(2**attempt)  # Exponential backoff

    return False


async def collect_user_targets(
    client: TelegramClient, mode: str
) -> List[Tuple[int, int]]:
    """
    Collect user targets from dialogs based on mode with comprehensive filtering

    Args:
        client: Authenticated Telethon client
        mode: Target mode ('archived', 'normal', 'all')

    Returns:
        List of (user_id, access_hash) tuples
    """
    targets = []
    processed_count = 0

    try:
        async for dialog in client.iter_dialogs():
            processed_count += 1

            # Only process user dialogs, skip groups/channels
            if not dialog.is_user:
                continue

            entity = dialog.entity
            if not entity:
                continue

            # Validate access hash
            access_hash = getattr(entity, "access_hash", None)
            if not access_hash:
                continue

            user_id = getattr(entity, "id", 0)
            if user_id <= 0:
                continue

            # Skip bots and deleted users
            if getattr(entity, "bot", False):
                continue

            if getattr(entity, "deleted", False):
                continue

            # Skip service accounts
            if user_id in SKIP_SERVICE_IDS:
                continue

            # Filter by folder based on mode
            folder_id = getattr(dialog, "folder_id", None)

            if mode == "archived" and folder_id != 1:
                continue
            elif mode == "normal" and folder_id is not None:
                continue
            # For 'all' mode, no additional filtering needed

            targets.append((user_id, access_hash))

        logging.info(
            f"Processed {processed_count} dialogs, found {len(targets)} valid targets for mode '{mode}'"
        )
        return targets

    except Exception as e:
        logging.error(f"Error collecting targets: {e}")
        return []


class BroadcastEngine:
    """Main broadcast engine for sending messages"""

    def __init__(self, config: BroadcastConfig):
        """Initialize broadcast engine with configuration"""
        self.config = config
        self.client = None
        self.history = safe_load_json(config.HISTORY_FILE)
        self.retry_queue = safe_load_json(config.RETRY_FILE)
        self.failed_users = safe_load_json(config.FAILED_FILE)

        # Runtime statistics
        self.sent_count = 0
        self.skip_count = 0
        self.fail_count = 0
        self.current_delay = config.MESSAGE_DELAY

        # Setup logging
        self.logger = logging.getLogger("broadcast_engine")

    async def initialize_client(self) -> bool:
        """Initialize and authenticate Telethon client"""
        try:
            session_path = self.config.get_session_path()
            self.client = TelegramClient(
                session_path, self.config.API_ID, self.config.API_HASH
            )

            await self.client.start()

            # Verify authentication
            me = await self.client.get_me()
            if not me:
                raise ConnectionError("Failed to authenticate with Telegram")

            self.logger.info(f"Successfully authenticated as user {me.id}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize client: {e}")
            return False

    async def send_status_notification(
        self, message: str, disable_notification: bool = False
    ) -> None:
        """Send status notification to bot chat"""
        chat_ids = [self.config.BOT_CHAT_ID, self.config.ADMIN_PROGRESS_CHAT_ID]

        for chat_id in chat_ids:
            if chat_id and self.config.BOT_TOKEN:
                send_bot_notification(
                    self.config.BOT_TOKEN, chat_id, message, disable_notification
                )

    async def run_broadcast(self) -> bool:
        """Execute the main broadcast operation"""
        try:
            # Initialize client
            if not await self.initialize_client():
                return False

            # Validate message content
            message_text = self.config.DEFAULT_MESSAGE
            if not message_text:
                self.logger.error("No message text provided")
                return False

            # Collect targets
            self.logger.info(f"Collecting targets with mode: {self.config.TARGET_MODE}")
            targets = await collect_user_targets(self.client, self.config.TARGET_MODE)

            if not targets:
                self.logger.warning("No targets found for broadcast")
                await self.send_status_notification("⚠️ No targets found for broadcast")
                return False

            self.logger.info(f"Starting broadcast to {len(targets)} targets")

            # Send start notification
            await self.send_status_notification(
                f"🚀 <b>Broadcast Started</b>\n"
                f"Account: <code>{self.config.ACCOUNT_FRIENDLY_NAME or 'Unknown'}</code>\n"
                f"Mode: <code>{self.config.TARGET_MODE}</code>\n"
                f"Targets: <b>{len(targets)}</b>"
            )

            # Process each target
            for i, (user_id, access_hash) in enumerate(targets, 1):
                try:
                    await self.process_target(user_id, access_hash, message_text)

                    # Periodic progress reports
                    if (
                        self.config.ADMIN_PROGRESS_CHAT_ID
                        and self.sent_count > 0
                        and self.sent_count % 100 == 0
                    ):
                        await self.send_status_notification(
                            f"📈 Прогресс: <b>{self.sent_count}</b> отправлено, <b>{self.fail_count}</b> ошибок\n"
                            f"Аккаунт: <code>{self.config.ACCOUNT_FRIENDLY_NAME or 'Unknown'}</code>",
                            disable_notification=True,
                        )

                    # Periodic data saves
                    if (self.sent_count + self.fail_count) % 50 == 0:
                        self.save_progress()

                    # Delay between messages
                    if i < len(targets):  # Don't delay after last message
                        await asyncio.sleep(self.current_delay)

                except Exception as e:
                    self.logger.error(f"Error processing target {user_id}: {e}")
                    self.fail_count += 1

            # Final save and notification
            self.save_progress()

            await self.send_status_notification(
                f"✅ <b>Broadcast Completed</b>\n"
                f"Account: <code>{self.config.ACCOUNT_FRIENDLY_NAME or 'Unknown'}</code>\n"
                f"Sent: <b>{self.sent_count}</b>\n"
                f"Failed: <b>{self.fail_count}</b>\n"
                f"Skipped: <b>{self.skip_count}</b>"
            )
            print(
                f"[RESULT] sent={self.sent_count} failed={self.fail_count} skipped={self.skip_count} total={len(targets)}"
            )

            self.logger.info(
                f"Broadcast completed: sent={self.sent_count}, failed={self.fail_count}, skipped={self.skip_count}"
            )
            return True

        except Exception as e:
            self.logger.error(f"Broadcast failed: {e}")
            await self.send_status_notification(f"❌ Broadcast failed: {str(e)}")
            return False

        finally:
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception as e:
                    self.logger.warning(f"Error disconnecting client: {e}")

    async def process_target(
        self, user_id: int, access_hash: int, message_text: str
    ) -> None:
        """Process individual target user"""
        user_key = str(user_id)

        # Check if already processed
        if user_key in self.history:
            self.skip_count += 1
            return

        if user_key in self.failed_users:
            self.skip_count += 1
            return

        try:
            # Create input peer and send message
            peer = InputPeerUser(user_id, access_hash)
            await self.client.send_message(peer, message_text)

            # Mark as successful
            self.history[user_key] = {"sent_at": time.time(), "status": "success"}
            self.sent_count += 1

            if self.sent_count % 10 == 0:
                self.logger.info(
                    f"Progress: {self.sent_count} sent, {self.fail_count} failed"
                )
            else:
                self.logger.debug(f"Message sent successfully to user {user_id}")

        except errors.FloodWaitError as e:
            # Handle rate limiting
            wait_seconds = getattr(e, "seconds", 60)
            self.logger.warning(f"Rate limited: waiting {wait_seconds} seconds")

            # Add to retry queue
            self.retry_queue[user_key] = {
                "user_id": user_id,
                "access_hash": access_hash,
                "retry_after": time.time() + wait_seconds,
                "attempts": self.retry_queue.get(user_key, {}).get("attempts", 0) + 1,
            }

            # Implement exponential backoff
            await asyncio.sleep(wait_seconds)
            self.current_delay = min(
                self.current_delay * 1.5, self.config.MAX_MESSAGE_DELAY
            )

            # Try to send again immediately after waiting
            try:
                peer = InputPeerUser(user_id, access_hash)
                await self.client.send_message(peer, message_text)

                # Mark as successful
                self.history[user_key] = {"sent_at": time.time(), "status": "success"}
                self.sent_count += 1

                # Remove from retry queue
                if user_key in self.retry_queue:
                    del self.retry_queue[user_key]

                self.logger.info(
                    f"Message sent successfully to user {user_id} after flood wait"
                )

            except errors.FloodWaitError as retry_flood:
                # Another FloodWait - don't retry again, just mark for later
                retry_wait = getattr(retry_flood, "seconds", 60)
                self.logger.warning(
                    f"FloodWait again for user {user_id}: {retry_wait}s - skipping"
                )
                self.retry_queue[user_key]["retry_after"] = time.time() + retry_wait
                self.retry_queue[user_key]["attempts"] += 1
                self.skip_count += 1

            except Exception as retry_error:
                # If retry fails, mark as failed
                self.failed_users[user_key] = {
                    "error": f"FloodWait retry failed: {retry_error}",
                    "failed_at": time.time(),
                    "permanent": False,
                }
                self.fail_count += 1
                self.logger.warning(f"Retry failed for user {user_id}: {retry_error}")

        except (
            errors.UserPrivacyRestrictedError,
            errors.UserDeactivatedError,
            errors.UserDeactivatedBanError,
        ) as e:
            # User restrictions - mark as failed permanently
            self.failed_users[user_key] = {
                "error": str(e),
                "failed_at": time.time(),
                "permanent": True,
            }
            self.fail_count += 1
            self.logger.debug(f"User {user_id} failed permanently: {e}")

        except (errors.PeerIdInvalidError, errors.UserIdInvalidError) as e:
            # Invalid user - mark as failed
            self.failed_users[user_key] = {
                "error": str(e),
                "failed_at": time.time(),
                "permanent": True,
            }
            self.fail_count += 1
            # Removed duplicate skip_count increment - already counted in fail_count
            self.logger.debug(f"Invalid user {user_id}: {type(e).__name__}")

        except Exception as e:
            # Other errors - mark for potential retry
            self.failed_users[user_key] = {
                "error": str(e),
                "failed_at": time.time(),
                "permanent": False,
            }
            self.fail_count += 1
            self.logger.warning(f"Failed to send to user {user_id}: {e}")

    def save_progress(self) -> None:
        """Save current progress to files"""
        try:
            safe_save_json(self.config.HISTORY_FILE, self.history)
            safe_save_json(self.config.FAILED_FILE, self.failed_users)
            safe_save_json(self.config.RETRY_FILE, self.retry_queue)
            self.logger.debug("Progress saved successfully")
        except Exception as e:
            self.logger.error(f"Failed to save progress: {e}")


def main():
    """Main entry point"""
    # Setup logging with file handler
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Create logs directory if it doesn't exist
    logs_dir = os.getenv("LOGS_DIR", "logs")
    os.makedirs(logs_dir, mode=0o700, exist_ok=True)

    # Setup handlers
    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(logs_dir, "messages.log"), encoding="utf-8"),
    ]

    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )

    logger = logging.getLogger("main")

    try:
        # Load configuration
        config = BroadcastConfig()
        logger.info("Configuration loaded successfully")

        # Create and run broadcast engine
        engine = BroadcastEngine(config)
        success = asyncio.run(engine.run_broadcast())

        if success:
            logger.info("Broadcast completed successfully")
            exit(0)
        else:
            logger.error("Broadcast failed")
            exit(1)

    except ConfigurationError as e:
        logger.error(f"Configuration error: {e}")
        exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        exit(1)


if __name__ == "__main__":
    main()
