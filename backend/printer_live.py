"""Background loop for automatic printer status synchronization."""
import asyncio
import logging

logger = logging.getLogger("printer_live")

STATUS_INTERVAL = 10     # seconds between DB status polls


async def _status_loop(db_maker):
    from printer_sync import sync_all
    while True:
        try:
            async with db_maker() as db:
                await sync_all(db)
        except Exception as e:
            logger.warning("auto-sync error: %s", e)
        await asyncio.sleep(STATUS_INTERVAL)


async def start_background_tasks(db_maker):
    """Called from lifespan after DB is ready. Starts the status sync loop and
    the upload-directory cleanup sweep."""
    from upload_cleanup import cleanup_loop

    asyncio.create_task(_status_loop(db_maker))
    asyncio.create_task(cleanup_loop(db_maker))
