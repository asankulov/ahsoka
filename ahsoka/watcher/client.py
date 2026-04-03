from pyrogram import Client

from ahsoka.config import Settings


def build_pyrogram_client(settings: Settings) -> Client:
    return Client(
        name=settings.session_name,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )
