from config import celery_app, db, BOT_TOKEN
from aiogram import Bot
from datetime import datetime, timedelta
from bson import ObjectId
import humanize
import logging
import pytz

# Инициализация бота и логгера
bot = Bot(token=BOT_TOKEN)
logger = logging.getLogger(__name__)
timezone_moscow = pytz.timezone("Europe/Moscow")

def convert_to_local_time(utc_time):
    """Конвертирует UTC время в московское (UTC+3)"""
    if utc_time.tzinfo is None:
        utc_time = pytz.utc.localize(utc_time)
    return utc_time.astimezone(timezone_moscow)

@celery_app.task
def send_reminder(task_id: str):
    """Отправка напоминания для задачи"""
    try:
        task = db.tasks.find_one({"_id": ObjectId(task_id)})
        
        # Проверка актуальности задачи
        if not task or task["status"] != "active":
            return

        now = datetime.now(pytz.utc)
        task_deadline = task["deadline"]
        
        # Приведение времени к UTC, если необходимо
        if task_deadline.tzinfo is None:
            task_deadline = pytz.utc.localize(task_deadline)
        
        # Проверка просрочки
        if now > task_deadline:
            logger.info(f"Задача {task_id} просрочена")
            return

        # Формирование сообщения
        time_left = task_deadline - now
        local_deadline = convert_to_local_time(task_deadline)
        
        message = (
            f"🔔 Напоминание!\n"
            f"Задача: {task['description']}\n"
            f"Дедлайн: {local_deadline.strftime('%d.%m.%Y %H:%M')}\n"
            f"Осталось: {humanize.naturaldelta(time_left)}"
        )
        
        bot.send_message(task["user_id"], message)
        
    except Exception as e:
        logger.error(f"Ошибка отправки напоминания: {str(e)}")

@celery_app.task
def check_deadlines():
    """Проверка и пометка просроченных задач"""
    now = datetime.now(pytz.utc)
    logger.info("Запущена проверка просроченных задач...")
    
    try:
        # Поиск активных задач с истекшим дедлайном
        expired_tasks = list(db.tasks.find({
            "deadline": {"$lt": now},
            "status": "active"
        }))
        
        for task in expired_tasks:
            # Обновление статуса
            db.tasks.update_one(
                {"_id": task["_id"]},
                {"$set": {"status": "overdue"}}
            )
            
            # Конвертация времени для уведомления
            task_deadline = task["deadline"]
            if task_deadline.tzinfo is None:
                task_deadline = pytz.utc.localize(task_deadline)
            
            local_deadline = convert_to_local_time(task_deadline)
            
            # Отправка уведомления
            bot.send_message(
                task["user_id"],
                f"❌ Задача просрочена!\n"
                f"Название: {task['description']}\n"
                f"Дедлайн был: {local_deadline.strftime('%d.%m.%Y %H:%M')}"
            )
            
        logger.info(f"Обработано {len(expired_tasks)} просроченных задач")
            
    except Exception as e:
        logger.error(f"Ошибка проверки дедлайнов: {str(e)}")

# Настройка периодических задач
celery_app.conf.beat_schedule = {
    'check-deadlines-every-minute': {
        'task': 'tasks.check_deadlines',
        'schedule': 60.0,  # Каждые 60 секунд
    },
}