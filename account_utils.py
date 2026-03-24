#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Account Management Utilities
Session validation, cleanup, and maintenance tools
"""

import os
import sys
import asyncio
import json
import shutil
import re
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from dotenv import load_dotenv

# Add current directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

# Constants
DEFAULT_SESSION_DIR = ".sessions"
MIN_PHONE_LENGTH = 7
MAX_PHONE_LENGTH = 20
SESSION_FILE_PERMISSIONS = 0o600  # Read/write for owner only


class ValidationError(Exception):
    """Raised when validation fails"""
    pass


class NetworkError(Exception):
    """Raised when network operations fail"""
    pass


class ConfigurationError(Exception):
    """Raised when configuration is invalid"""
    pass


def validate_phone_number(phone: str) -> Tuple[bool, str]:
    """Validate phone number format"""
    if not phone or not isinstance(phone, str):
        return False, "Phone number is required"

    phone = phone.strip()
    if not phone.startswith("+"):
        return False, "Phone number must start with +"

    # Extract digits only
    digits = re.sub(r'[^\d]', '', phone[1:])

    if not digits:
        return False, "Phone number must contain digits"

    if len(digits) < MIN_PHONE_LENGTH:
        return False, f"Phone number too short (minimum {MIN_PHONE_LENGTH} digits)"

    if len(phone) > MAX_PHONE_LENGTH:
        return False, f"Phone number too long (maximum {MAX_PHONE_LENGTH} characters)"

    return True, ""


def get_environment_config() -> Tuple[int, str, str]:
    """Get and validate environment configuration"""
    api_id_str = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    session_dir = os.getenv("SESSION_DIR", DEFAULT_SESSION_DIR)

    if not api_id_str:
        raise ConfigurationError("TELEGRAM_API_ID not found in environment")

    if not api_hash:
        raise ConfigurationError("TELEGRAM_API_HASH not found in environment")

    try:
        api_id = int(api_id_str)
    except ValueError:
        raise ConfigurationError("TELEGRAM_API_ID must be a valid integer")

    return api_id, api_hash, session_dir


async def check_session_status(session_path: str, api_id: int, api_hash: str) -> Dict[str, Any]:
    """
    Check the status of a Telegram session

    Args:
        session_path: Path to the session file
        api_id: Telegram API ID
        api_hash: Telegram API hash

    Returns:
        Dictionary with session status information
    """
    try:
        from telethon import TelegramClient
        from telethon.errors import (
            AuthKeyUnregisteredError,
            UserDeactivatedError,
            UserDeactivatedBanError,
        )

        # Remove .session extension if present
        if session_path.endswith('.session'):
            session_path = session_path[:-8]

        result: Dict[str, Any] = {
            'valid': False,
            'authorized': False,
            'user_info': None,
            'error': None,
            'session_file_exists': os.path.exists(f"{session_path}.session"),
        }

        if not result['session_file_exists']:
            result['error'] = "Session file does not exist"
            return result

        client = TelegramClient(session_path, api_id, api_hash)

        try:
            await asyncio.wait_for(client.connect(), timeout=30)

            if await client.is_user_authorized():
                try:
                    me = await client.get_me()
                    if me:
                        result['valid'] = True
                        result['authorized'] = True
                        result['user_info'] = {
                            'id': me.id,
                            'first_name': getattr(me, 'first_name', None) or '',
                            'last_name': getattr(me, 'last_name', None) or '',
                            'username': getattr(me, 'username', None),
                            'phone': getattr(me, 'phone', None),
                            'is_bot': getattr(me, 'bot', False),
                            'is_verified': getattr(me, 'verified', False),
                        }
                    else:
                        result['error'] = "Failed to retrieve user information"

                except (AuthKeyUnregisteredError, UserDeactivatedError, UserDeactivatedBanError) as e:
                    result['error'] = f"Account deactivated or banned: {e}"

                except Exception as e:
                    result['error'] = f"Error retrieving user info: {e}"
            else:
                result['error'] = "Session not authorized"

        except asyncio.TimeoutError:
            result['error'] = "Connection timeout"
        except Exception as e:
            result['error'] = f"Connection error: {e}"
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass  # Ignore disconnect errors

        return result

    except ImportError:
        return {
            'valid': False,
            'authorized': False,
            'user_info': None,
            'error': "Telethon not installed",
        }
    except Exception as e:
        return {
            'valid': False,
            'authorized': False,
            'user_info': None,
            'error': f"Unexpected error: {e}",
        }


async def validate_all_sessions() -> None:
    """Check all session files in the configured directory"""
    try:
        api_id, api_hash, session_dir = get_environment_config()
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        return

    if not os.path.exists(session_dir):
        print(f"Session directory '{session_dir}' does not exist")
        return

    try:
        session_files = [f for f in os.listdir(session_dir) if f.endswith('.session')]
    except OSError as e:
        print(f"Error reading session directory: {e}")
        return

    if not session_files:
        print(f"No session files found in '{session_dir}'")
        return

    print(f"Checking {len(session_files)} session file(s)...\n")

    valid_count = 0
    invalid_count = 0

    for session_file in sorted(session_files):
        session_path = os.path.join(session_dir, session_file)
        session_name = session_file.replace('.session', '')

        print(f"Checking {session_name}...")

        try:
            result = await check_session_status(session_path, api_id, api_hash)

            if result['valid'] and result['authorized'] and result['user_info']:
                user = result['user_info']

                # Build display name
                name_parts = [user['first_name'], user['last_name']]
                display_name = ' '.join(part for part in name_parts if part).strip()
                if not display_name:
                    display_name = "No name"

                username_display = f"@{user['username']}" if user['username'] else 'no username'
                phone_display = user['phone'] if user['phone'] else 'unknown'

                status_indicators: List[str] = []
                if user['is_bot']:
                    status_indicators.append('BOT')
                if user['is_verified']:
                    status_indicators.append('VERIFIED')

                status_text = f" [{'/'.join(status_indicators)}]" if status_indicators else ""

                print(f"   ✅ {display_name} {username_display}{status_text}")
                print(f"      Phone: {phone_display} | ID: {user['id']}")
                valid_count += 1

            else:
                print(f"   ❌ ERROR: {result['error']}")
                invalid_count += 1

        except Exception as e:
            print(f"   ❌ UNEXPECTED ERROR: {e}")
            invalid_count += 1

        print()

    print("=" * 60)
    print(f"Validation Summary:")
    print(f"   Valid sessions: {valid_count}")
    print(f"   Invalid sessions: {invalid_count}")
    print(f"   Total files: {len(session_files)}")

    if invalid_count > 0:
        print(f"\nRecommendation: Run 'clean' command to remove invalid sessions")


def create_session_backup() -> None:
    """Create a backup of all session files"""
    try:
        _, _, session_dir = get_environment_config()
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        return

    if not os.path.exists(session_dir):
        print(f"Session directory '{session_dir}' does not exist")
        return

    try:
        session_files = [f for f in os.listdir(session_dir) if f.endswith('.session')]
    except OSError as e:
        print(f"Error reading session directory: {e}")
        return

    if not session_files:
        print(f"No session files found in '{session_dir}'")
        return

    # Create backup directory
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = f"session_backup_{timestamp}"

    try:
        os.makedirs(backup_dir, mode=0o700, exist_ok=True)

        copied_count = 0
        for session_file in session_files:
            src_path = os.path.join(session_dir, session_file)
            dst_path = os.path.join(backup_dir, session_file)

            try:
                shutil.copy2(src_path, dst_path)
                # Set secure permissions
                os.chmod(dst_path, SESSION_FILE_PERMISSIONS)
                copied_count += 1
            except OSError as e:
                print(f"Failed to copy {session_file}: {e}")

        # Create backup info file
        backup_info = {
            "backup_date": datetime.now().isoformat(),
            "total_sessions": copied_count,
            "session_files": session_files,
            "source_directory": session_dir,
            "backup_directory": backup_dir,
        }

        info_path = os.path.join(backup_dir, "backup_info.json")
        try:
            with open(info_path, 'w', encoding='utf-8') as f:
                json.dump(backup_info, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"Warning: Failed to create backup info file: {e}")

        print(f"Backup created successfully:")
        print(f"   Directory: {backup_dir}")
        print(f"   Files copied: {copied_count}")
        print(f"   Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    except OSError as e:
        print(f"Failed to create backup: {e}")


def restore_session_backup(backup_dir: str) -> None:
    """Restore session files from backup"""
    if not backup_dir:
        print("Backup directory path is required")
        return

    if not os.path.exists(backup_dir):
        print(f"Backup directory '{backup_dir}' does not exist")
        return

    try:
        _, _, session_dir = get_environment_config()
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        return

    # Read backup info if available
    info_path = os.path.join(backup_dir, "backup_info.json")
    if os.path.exists(info_path):
        try:
            with open(info_path, 'r', encoding='utf-8') as f:
                backup_info = json.load(f)

            print(f"Backup Information:")
            print(f"   Date: {backup_info.get('backup_date', 'unknown')}")
            print(f"   Files: {backup_info.get('total_sessions', 0)}")
            print(f"   Source: {backup_info.get('source_directory', 'unknown')}")

        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: Could not read backup info: {e}")

    # Find session files to restore
    try:
        session_files = [f for f in os.listdir(backup_dir) if f.endswith('.session')]
    except OSError as e:
        print(f"Error reading backup directory: {e}")
        return

    if not session_files:
        print(f"No session files found in backup directory")
        return

    print(f"\nWARNING: This will overwrite existing session files!")
    print(f"Found {len(session_files)} session file(s) to restore.")
    print("Continue? (type 'yes' to confirm): ", end='')

    try:
        confirmation = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nOperation cancelled by user")
        return

    if confirmation != 'yes':
        print("Operation cancelled")
        return

    try:
        # Create target directory if it doesn't exist
        os.makedirs(session_dir, mode=0o700, exist_ok=True)

        restored_count = 0
        for session_file in session_files:
            src_path = os.path.join(backup_dir, session_file)
            dst_path = os.path.join(session_dir, session_file)

            try:
                shutil.copy2(src_path, dst_path)
                # Set secure permissions
                os.chmod(dst_path, SESSION_FILE_PERMISSIONS)
                print(f"Restored: {session_file}")
                restored_count += 1
            except OSError as e:
                print(f"Failed to restore {session_file}: {e}")

        print(f"\nRestore completed:")
        print(f"   Target directory: {session_dir}")
        print(f"   Files restored: {restored_count}")

    except OSError as e:
        print(f"Restore failed: {e}")


async def get_session_detailed_info(session_name: str) -> None:
    """Get detailed information about a specific session"""
    try:
        api_id, api_hash, session_dir = get_environment_config()
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        return

    # Normalize session name
    if session_name.endswith('.session'):
        session_file = session_name
        session_name = session_name[:-8]
    else:
        session_file = f"{session_name}.session"

    session_path = os.path.join(session_dir, session_file)

    if not os.path.exists(session_path):
        print(f"Session '{session_name}' not found")
        print(f"Path checked: {session_path}")
        return

    print(f"Analyzing session: {session_name}\n")

    # File system information
    try:
        stat_info = os.stat(session_path)
        size_bytes = stat_info.st_size
        size_kb = size_bytes / 1024
        modified_time = datetime.fromtimestamp(stat_info.st_mtime)
        access_time = datetime.fromtimestamp(stat_info.st_atime)

        print(f"File Information:")
        print(f"   Name: {session_file}")
        print(f"   Size: {size_kb:.1f} KB ({size_bytes} bytes)")
        print(f"   Modified: {modified_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Accessed: {access_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Permissions: {oct(stat_info.st_mode)[-3:]}")
        print()

    except OSError as e:
        print(f"Error reading file information: {e}\n")

    # Telegram session information
    try:
        result = await check_session_status(session_path, api_id, api_hash)

        print(f"Session Status:")
        print(f"   File exists: {'Yes' if result['session_file_exists'] else 'No'}")
        print(f"   Valid: {'Yes' if result['valid'] else 'No'}")
        print(f"   Authorized: {'Yes' if result['authorized'] else 'No'}")

        if result['valid'] and result['authorized'] and result['user_info']:
            user = result['user_info']

            print(f"\nUser Information:")
            print(f"   ID: {user['id']}")

            name_parts = [user['first_name'], user['last_name']]
            display_name = ' '.join(part for part in name_parts if part).strip()
            print(f"   Name: {display_name or 'No name set'}")

            if user['username']:
                print(f"   Username: @{user['username']}")
            else:
                print(f"   Username: Not set")

            print(f"   Phone: {user['phone'] or 'Unknown'}")
            print(f"   Is Bot: {'Yes' if user['is_bot'] else 'No'}")
            print(f"   Verified: {'Yes' if user['is_verified'] else 'No'}")

        else:
            print(f"\nError: {result['error']}")

    except Exception as e:
        print(f"Error checking session status: {e}")


async def clean_invalid_sessions() -> None:
    """Remove invalid session files (WARNING: Irreversible operation!)"""
    try:
        api_id, api_hash, session_dir = get_environment_config()
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        return

    if not os.path.exists(session_dir):
        print(f"Session directory '{session_dir}' does not exist")
        return

    try:
        session_files = [f for f in os.listdir(session_dir) if f.endswith('.session')]
    except OSError as e:
        print(f"Error reading session directory: {e}")
        return

    if not session_files:
        print(f"No session files found in '{session_dir}'")
        return

    print("⚠️  WARNING: This operation will permanently delete invalid session files!")
    print(f"Found {len(session_files)} session file(s) to check.")
    print("Are you sure you want to continue? (type 'yes' to confirm): ", end='')

    try:
        confirmation = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nOperation cancelled by user")
        return

    if confirmation != 'yes':
        print("Operation cancelled")
        return

    print(f"\nChecking sessions for cleanup...\n")

    deleted_count = 0
    preserved_count = 0

    for session_file in sorted(session_files):
        session_path = os.path.join(session_dir, session_file)
        session_name = session_file.replace('.session', '')

        try:
            result = await check_session_status(session_path, api_id, api_hash)

            if result['valid'] and result['authorized'] and result['user_info']:
                user = result['user_info']
                name_parts = [user['first_name'], user['last_name']]
                display_name = ' '.join(part for part in name_parts if part).strip()
                if not display_name:
                    display_name = "No name"

                print(f"Preserved: {session_name} - {display_name}")
                preserved_count += 1

            else:
                try:
                    os.remove(session_path)
                    print(f"Deleted: {session_name} - {result['error']}")
                    deleted_count += 1
                except OSError as e:
                    print(f"Failed to delete {session_name}: {e}")

        except Exception as e:
            print(f"Error processing {session_name}: {e}")

    print(f"\nCleanup completed:")
    print(f"   Deleted sessions: {deleted_count}")
    print(f"   Preserved sessions: {preserved_count}")


def main() -> None:
    """Main entry point for command-line usage"""
    if len(sys.argv) < 2:
        print("Telegram Account Management Utilities\n")
        print("Usage:")
        print("  python account_utils.py check                    - Check all sessions")
        print("  python account_utils.py clean                    - Remove invalid sessions")
        print("  python account_utils.py backup                   - Create backup")
        print("  python account_utils.py restore <backup_dir>     - Restore from backup")
        print("  python account_utils.py info <session_name>      - Get session details")
        print("\nExamples:")
        print("  python account_utils.py check")
        print("  python account_utils.py info 1234567890")
        print("  python account_utils.py restore session_backup_20240101_120000")
        print("\nNote: Session files are stored in the directory specified by SESSION_DIR")
        print("      environment variable (default: .sessions)")
        return

    command = sys.argv[1].lower()

    try:
        if command == "check":
            asyncio.run(validate_all_sessions())

        elif command == "clean":
            asyncio.run(clean_invalid_sessions())

        elif command == "backup":
            create_session_backup()

        elif command == "restore":
            if len(sys.argv) < 3:
                print("Error: Backup directory path required")
                print("Usage: python account_utils.py restore <backup_directory>")
                return
            backup_directory = sys.argv[2]
            restore_session_backup(backup_directory)

        elif command == "info":
            if len(sys.argv) < 3:
                print("Error: Session name required")
                print("Usage: python account_utils.py info <session_name>")
                return
            session_name = sys.argv[2]
            asyncio.run(get_session_detailed_info(session_name))

        else:
            print(f"Unknown command: {command}")
            print("Available commands: check, clean, backup, restore, info")

    except KeyboardInterrupt:
        print("\nOperation interrupted by user")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
