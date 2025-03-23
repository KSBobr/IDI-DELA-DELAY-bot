import os
from celery import Celery
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

# MongoDB
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
mongo_client = MongoClient(MONGO_URI)
db = mongo_client.IDdelbot

# Celery
celery_app = Celery(
    'tasks',
    broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    include=['tasks']
)

celery_app.conf.update(
    task_serializer='json',
    result_serializer='json',
    timezone='Europe/Moscow',
    enable_utc=True,
    broker_connection_retry_on_startup=True
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DEFAULT_REMINDER = 30  # Минуты