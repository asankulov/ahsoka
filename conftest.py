"""Root conftest: inject dummy env vars before any ahsoka module is imported.

ahsoka/config.py instantiates Settings() at module level, so the required
environment variables must be present before import time.
"""
import os

os.environ.setdefault("TELEGRAM_API_ID", "12345")
os.environ.setdefault("TELEGRAM_API_HASH", "dummyhash")
os.environ.setdefault("BOT_TOKEN", "123456:AADummyBotToken")
os.environ.setdefault("OWNER_CHAT_ID", "12345")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-dummy")
