import asyncio
from app.worker.worker import run_worker

if __name__ == "__main__":
    asyncio.run(run_worker())
