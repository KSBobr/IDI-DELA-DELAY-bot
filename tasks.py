# tasks.py
import os
import logging
import pytz
import asyncio
from datetime import datetime
from bson import ObjectId
from aiogram import Bot, types
from config import celery_app, db
from aiogram.utils.keyboard import InlineKeyboardBuilder
import humanize

logger = logging.getLogger(__name__)
timezone_moscow = pytz.timezone("Europe/Moscow")
bot = Bot(token=os.getenv("BOT_TOKEN"))

# Создаем глобальный цикл событий
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

def convert_to_local_time(utc_time):
    if utc_time.tzinfo is None:
        utc_time = pytz.utc.localize(utc_time)
    return utc_time.astimezone(timezone_moscow)

async def async_send_message(user_id: int, text: str, reply_markup=None):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {str(e)}")

def run_async(coro):
    """Запуск асинхронных функций в глобальном цикле"""
    return loop.run_until_complete(coro)

@celery_app.task(name="tasks.send_reminder")
def send_reminder(task_id: str):
    try:
        task = db.tasks.find_one({"_id": ObjectId(task_id)})
        
        if not task or task["status"] != "active":
            return

        now = datetime.now(pytz.utc)
        task_deadline = task["deadline"]
        
        if task_deadline.tzinfo is None:
            task_deadline = pytz.utc.localize(task_deadline)
        
        time_left = task_deadline - now
        local_deadline = convert_to_local_time(task_deadline)
        
        message = (
            f"🔔 До задачи '{task['description']}' осталось {humanize.naturaldelta(time_left)}.\n"
            "Будь готов делать дела! 🚀"
        )
        
        run_async(async_send_message(
            user_id=task["user_id"],
            text=message
        ))
        
    except Exception as e:
        logger.error(f"Ошибка отправки напоминания: {str(e)}", exc_info=True)

@celery_app.task(name="tasks.check_deadlines")
def check_deadlines():
    try:
        now = datetime.now(pytz.utc)
        logger.info(f"Запуск проверки дедлайнов в {now.isoformat()}")
        
        expired_tasks = list(db.tasks.find({
            "deadline": {"$lt": now},
            "status": "active"
        }))
        
        logger.info(f"Найдено {len(expired_tasks)} просроченных задач")
        
        for task in expired_tasks:
            db.tasks.update_one(
                {"_id": task["_id"]},
                {"$set": {"status": "overdue"}}
            )
            
            local_deadline = convert_to_local_time(task["deadline"])
            keyboard = InlineKeyboardBuilder()
            keyboard.row(
                types.InlineKeyboardButton(
                    text="⏳ Оставить как есть",
                    callback_data=f"keep_overdue_{task['_id']}"
                ),
                types.InlineKeyboardButton(
                    text="📅 Перенести на день",
                    callback_data=f"reschedule_{task['_id']}"
                )
            )
            
            run_async(
                async_send_message(
                    user_id=task["user_id"],
                    text=(
                        f"❌ Ты просрочил задачу: {task['description']}\n"
                        f"Дедлайн был: {local_deadline.strftime('%d.%m.%Y %H:%M')}"
                    ),
                    reply_markup=keyboard.as_markup()
                )
            )
            
    except Exception as e:
        logger.error(f"Критическая ошибка в check_deadlines: {str(e)}", exc_info=True)

celery_app.conf.beat_schedule = {
    'check-deadlines-every-minute': {
        'task': 'tasks.check_deadlines',
        'schedule': 60.0,
    },
}