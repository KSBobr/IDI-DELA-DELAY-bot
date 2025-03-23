import logging
import pytz
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from datetime import datetime, timedelta
from mongodb import User, Task
from config import db, celery_app, DEFAULT_REMINDER, BOT_TOKEN
from bson import ObjectId
import humanize
from bson.errors import InvalidId

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)
timezone_moscow = pytz.timezone("Europe/Moscow")

class TaskStates(StatesGroup):
    description = State()
    deadline = State()
    tags = State()
    reminder = State()
    edit_description = State()
    edit_deadline = State()
    edit_tags = State()
    edit_reminder = State()
    edit_custom_reminder = State()
    delete_date = State()
    tag_creation = State()
    tag_deletion = State()

def convert_to_local_time(utc_time):
    if utc_time.tzinfo is None:
        utc_time = pytz.utc.localize(utc_time)
    return utc_time.astimezone(timezone_moscow)

def main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(
        types.KeyboardButton(text="➕ Новая задача"),
        types.KeyboardButton(text="📝 Мои задачи")
    )
    builder.row(
        types.KeyboardButton(text="🏷️ Мои теги"),
        types.KeyboardButton(text="🗑️ Удалить задачи")
    )
    return builder.as_markup(resize_keyboard=True)

@dp.message(Command("start"))
async def start(message: types.Message):
    User.get_or_create(message.from_user.id)
    await message.answer(
        "🚀 Добро пожаловать в Task Manager!",
        reply_markup=main_keyboard()
    )

@dp.message(lambda m: m.text == "➕ Новая задача")
async def add_task_start(message: types.Message, state: FSMContext):
    await state.set_state(TaskStates.description)
    await message.answer("📝 Введите описание задачи:", reply_markup=types.ReplyKeyboardRemove())

@dp.message(TaskStates.description)
async def process_description(message: types.Message, state: FSMContext):
    if len(message.text) > 200:
        await message.answer("❌ Описание слишком длинное (макс. 200 символов)")
        return
        
    await state.update_data(description=message.text)
    await state.set_state(TaskStates.deadline)
    await message.answer("⏳ Введите дедлайн (ГГГГ-ММ-ДД ЧЧ:ММ):")

@dp.message(TaskStates.deadline)
async def process_deadline(message: types.Message, state: FSMContext):
    try:
        naive_deadline = datetime.strptime(message.text, "%Y-%m-%d %H:%M")
        local_deadline = timezone_moscow.localize(naive_deadline)
        utc_deadline = local_deadline.astimezone(pytz.utc)
        
        await state.update_data(deadline=utc_deadline)
        
        user = User.get_or_create(message.from_user.id)
        user_tags = user.get("tags", [])
        if user_tags:
            builder = InlineKeyboardBuilder()
            for tag in user_tags:
                builder.add(types.InlineKeyboardButton(
                    text=tag,
                    callback_data=f"tag_{tag}"
                ))
            builder.row(types.InlineKeyboardButton(
                text="🏁 Готово",
                callback_data="done_tags"
            ))
            await message.answer("🏷️ Выберите теги:", reply_markup=builder.as_markup())
            await state.set_state(TaskStates.tags)
        else:
            await state.update_data(tags=[])
            await state.set_state(TaskStates.reminder)
            await message.answer("⏰ Введите время в минутах до дедлайна для напоминания:")
    except ValueError:
        await message.answer("❌ Неверный формат даты! Попробуйте снова (ГГГГ-ММ-ДД ЧЧ:ММ):")

@dp.callback_query(TaskStates.tags, lambda c: c.data.startswith("tag_"))
async def process_tags(callback: types.CallbackQuery, state: FSMContext):
    tag = callback.data.split("_")[1]
    data = await state.get_data()
    tags = data.get("tags", [])
    if tag in tags:
        tags.remove(tag)
    else:
        tags.append(tag)
    await state.update_data(tags=tags)
    await callback.answer(f"Тег '{tag}' {'добавлен' if tag in tags else 'удалён'}!")

@dp.callback_query(TaskStates.tags, lambda c: c.data == "done_tags")
async def finish_tags(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TaskStates.reminder)
    await callback.message.answer("⏰ Введите время в минутах до дедлайна для напоминания:")

@dp.message(TaskStates.reminder)
async def custom_reminder(message: types.Message, state: FSMContext):
    try:
        minutes = int(message.text)
        if minutes <= 0:
            await message.answer("❌ Введите положительное число минут!")
            return
            
        data = await state.get_data()
        deadline = data["deadline"]
        reminder_time = deadline - timedelta(minutes=minutes)
        
        await state.update_data(reminder_time=reminder_time)
        await process_task_creation(message, state)
    except ValueError:
        await message.answer("❌ Введите целое число минут!")
        return

async def process_task_creation(message: types.Message, state: FSMContext):
    try:
        data = await state.get_data()
        user = User.get_or_create(message.from_user.id)
        
        task = {
            "user_id": message.from_user.id,
            "description": data["description"],
            "deadline": data["deadline"],
            "reminder_time": data["reminder_time"],
            "tags": data.get("tags", []),
            "status": "active",
            "created_at": datetime.now(pytz.utc)
        }
        
        result = Task.create(task)
        if not result.inserted_id:
            raise ValueError("Ошибка сохранения задачи")
            
        task_id = str(result.inserted_id)

        celery_app.send_task(
            "tasks.send_reminder",
            args=(task_id,),
            eta=task["reminder_time"].astimezone(pytz.utc)
        )

        local_reminder = convert_to_local_time(task["reminder_time"])
        await message.answer(
            f"✅ Задача создана!\n"
            f"⏰ Напоминание: {local_reminder.strftime('%d.%m.%Y %H:%M')}",
            reply_markup=main_keyboard()
        )
        
    except Exception as e:
        logging.error(f"Ошибка создания задачи: {str(e)}")
        await message.answer("❌ Не удалось создать задачу")
        
    finally:
        await state.clear()

@dp.message(lambda m: m.text == "📝 Мои задачи")
async def list_tasks(message: types.Message):
    try:
        tasks = list(db.tasks.find({
            "user_id": message.from_user.id,
            "status": {"$in": ["active", "overdue"]}
        }))

        now = datetime.now(pytz.utc)
        for task in tasks:
            task_deadline = task["deadline"]
            if task_deadline.tzinfo is None:
                task_deadline = pytz.utc.localize(task_deadline)
            
            if task["status"] == "active" and now > task_deadline:
                db.tasks.update_one(
                    {"_id": task["_id"]},
                    {"$set": {"status": "overdue"}}
                )
                task["status"] = "overdue"

        if not tasks:
            return await message.answer("📭 У вас нет активных задач!")

        status_map = {
            "active": "⏳ Активна",
            "completed": "✅ Выполнена",
            "overdue": "❌ Просрочена"
        }

        for task in tasks:
            task_deadline = task["deadline"]
            task_reminder = task["reminder_time"]
            
            local_deadline = convert_to_local_time(task_deadline)
            local_reminder = convert_to_local_time(task_reminder)
            
            status = status_map.get(task["status"], "❓ Неизвестный статус")
            text = (
                f"📌 {task['description']}\n"
                f"{status}\n"
                f"⏳ Дедлайн: {local_deadline.strftime('%d.%m.%Y %H:%M')}\n"
                f"🔔 Напоминание: {local_reminder.strftime('%d.%m.%Y %H:%M')}\n"
                f"🏷️ Теги: {', '.join(task['tags']) if task['tags'] else 'нет'}"
            )
            
            builder = InlineKeyboardBuilder()
            builder.row(
                types.InlineKeyboardButton(
                    text="✅ Завершить",
                    callback_data=f"complete_{task['_id']}"
                ),
                types.InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=f"edit_task_{task['_id']}"
                ),
                types.InlineKeyboardButton(
                    text="🗑️ Удалить",
                    callback_data=f"delete_{task['_id']}"
                )
            )
            await message.answer(text, reply_markup=builder.as_markup())

    except Exception as e:
        logging.error(f"Ошибка при выводе задач: {str(e)}")
        await message.answer("❌ Произошла ошибка при загрузке задач")

@dp.callback_query(lambda c: c.data.startswith("complete_"))
async def complete_task(callback: types.CallbackQuery):
    task_id = callback.data.split("_")[1]
    try:
        Task.update(task_id, {"status": "completed"})
        await callback.message.edit_text("✅ Задача завершена!")
    except InvalidId:
        await callback.answer("❌ Неверный ID задачи")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("delete_"))
async def delete_task(callback: types.CallbackQuery):
    task_id = callback.data.split("_")[1]
    try:
        Task.delete(task_id)
        await callback.message.edit_text("🗑️ Задача удалена!")
    except InvalidId:
        await callback.answer("❌ Неверный ID задачи")
    await callback.answer()

@dp.message(lambda m: m.text == "🗑️ Удалить задачи")
async def delete_tasks_menu(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.add(
        types.InlineKeyboardButton(
            text="🗑️ Удалить по дате",
            callback_data="delete_by_date"
        ),
        types.InlineKeyboardButton(
            text="❌ Удалить все",
            callback_data="delete_all"
        )
    )
    await message.answer("Выберите действие:", reply_markup=builder.as_markup())

@dp.callback_query(lambda c: c.data == "delete_by_date")
async def delete_by_date_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TaskStates.delete_date)
    await callback.message.answer("Введите дату (ГГГГ-ММ-ДД):")

@dp.message(TaskStates.delete_date)
async def process_delete_date(message: types.Message, state: FSMContext):
    try:
        naive_date = datetime.strptime(message.text, "%Y-%m-%d")
        local_date = timezone_moscow.localize(naive_date)
        utc_date = local_date.astimezone(pytz.utc)
        
        Task.delete_by_date(message.from_user.id, utc_date)
        await message.answer(f"🗑️ Задачи на {local_date.strftime('%d.%m.%Y')} удалены!")
        await state.clear()
    except ValueError:
        await message.answer("❌ Неверный формат даты! Попробуйте снова (ГГГГ-ММ-ДД):")

@dp.callback_query(lambda c: c.data == "delete_all")
async def delete_all_tasks(callback: types.CallbackQuery):
    Task.delete_all(callback.from_user.id)
    await callback.message.answer("🗑️ Все задачи удалены!")
    await callback.answer()

@dp.message(lambda m: m.text == "🏷️ Мои теги")
async def manage_tags(message: types.Message):
    user = User.get_or_create(message.from_user.id)
    tags = user.get("tags", [])
    
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="➕ Создать тег", callback_data="create_tag"),
        types.InlineKeyboardButton(text="🗑️ Удалить тег", callback_data="delete_tag")
    )
    if tags:
        builder.row(
            types.InlineKeyboardButton(text="🔍 Фильтр по тегам", callback_data="filter_tags")
        )
    await message.answer(
        f"🏷️ Ваши теги: {', '.join(tags) if tags else 'пока нет тегов!'}",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(lambda c: c.data == "create_tag")
async def create_tag_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TaskStates.tag_creation)
    await callback.message.answer("Введите название нового тега:")

@dp.message(TaskStates.tag_creation)
async def process_tag_creation(message: types.Message, state: FSMContext):
    User.add_tag(message.from_user.id, message.text)
    await message.answer(f"🏷️ Тег '{message.text}' создан!", reply_markup=main_keyboard())
    await state.clear()

@dp.callback_query(lambda c: c.data == "delete_tag")
async def delete_tag_start(callback: types.CallbackQuery, state: FSMContext):
    user = User.get_or_create(callback.from_user.id)
    tags = user.get("tags", [])
    
    if not tags:
        await callback.answer("❌ Нет тегов для удаления!")
        return
    
    builder = InlineKeyboardBuilder()
    for tag in tags:
        builder.add(types.InlineKeyboardButton(
            text=tag,
            callback_data=f"delete_tag_{tag}"
        ))
    builder.adjust(2)
    await callback.message.answer("Выберите тег для удаления:", reply_markup=builder.as_markup())

@dp.callback_query(lambda c: c.data.startswith("delete_tag_"))
async def process_tag_deletion(callback: types.CallbackQuery):
    tag = callback.data.split("_")[2]
    User.remove_tag(callback.from_user.id, tag)
    Task.remove_tag_from_all_tasks(callback.from_user.id, tag)
    await callback.message.edit_text(f"🗑️ Тег '{tag}' удален из всех задач!")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "filter_tags")
async def filter_tags_menu(callback: types.CallbackQuery):
    user = User.get_or_create(callback.from_user.id)
    tags = user.get("tags", [])
    
    if not tags:
        await callback.answer("❌ Нет тегов для фильтрации!")
        return
    
    builder = InlineKeyboardBuilder()
    for tag in tags:
        builder.add(types.InlineKeyboardButton(
            text=tag,
            callback_data=f"filter_tag_{tag}"
        ))
    builder.adjust(2)
    await callback.message.answer("Выберите тег для фильтрации:", reply_markup=builder.as_markup())

@dp.callback_query(lambda c: c.data.startswith("filter_tag_"))
async def filter_tasks_by_tag(callback: types.CallbackQuery):
    tag = callback.data.split("_")[2]
    tasks = Task.get_tasks_by_tag(callback.from_user.id, tag)
    
    if not tasks:
        await callback.message.answer(f"📭 Нет задач с тегом '{tag}'!")
        return
    
    for task in tasks:
        task_deadline = task["deadline"]
        task_reminder = task["reminder_time"]
        
        local_deadline = convert_to_local_time(task_deadline)
        local_reminder = convert_to_local_time(task_reminder)
        
        text = (
            f"📌 {task['description']}\n"
            f"⏳ Дедлайн: {local_deadline.strftime('%d.%m.%Y %H:%M')}\n"
            f"🔔 Напоминание: {local_reminder.strftime('%d.%m.%Y %H:%M')}\n"
            f"🏷️ Теги: {', '.join(task['tags'])}"
        )
        await callback.message.answer(text)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("edit_task_"))
async def edit_task_start(callback: types.CallbackQuery, state: FSMContext):
    try:
        task_id = callback.data.split("_")[2]
        task = Task.get_task(task_id)
        await state.update_data(edit_task_id=task_id)
        await state.set_state(TaskStates.edit_description)
        await callback.message.answer(
            f"✏️ Текущее описание: {task['description']}\n"
            "Введите новое описание:"
        )
    except InvalidId:
        await callback.answer("❌ Неверный ID задачи")

@dp.message(TaskStates.edit_description)
async def edit_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(TaskStates.edit_deadline)
    await message.answer("Введите новый дедлайн (ГГГГ-ММ-ДД ЧЧ:ММ):")

@dp.message(TaskStates.edit_deadline)
async def edit_deadline(message: types.Message, state: FSMContext):
    try:
        naive_deadline = datetime.strptime(message.text, "%Y-%m-%d %H:%M")
        local_deadline = timezone_moscow.localize(naive_deadline)
        utc_deadline = local_deadline.astimezone(pytz.utc)
        
        await state.update_data(deadline=utc_deadline)
        
        user = User.get_or_create(message.from_user.id)
        user_tags = user.get("tags", [])
        data = await state.get_data()
        current_tags = data.get("tags", [])
        
        builder = InlineKeyboardBuilder()
        for tag in user_tags:
            builder.add(types.InlineKeyboardButton(
                text=f"✅ {tag}" if tag in current_tags else tag,
                callback_data=f"edit_tag_{tag}"
            ))
        builder.row(types.InlineKeyboardButton(
            text="🏁 Готово",
            callback_data="edit_done_tags"
        ))
        await message.answer("🏷️ Выберите теги:", reply_markup=builder.as_markup())
        await state.set_state(TaskStates.edit_tags)
        
    except ValueError:
        await message.answer("❌ Неверный формат даты! Попробуйте снова (ГГГГ-ММ-ДД ЧЧ:ММ):")

@dp.callback_query(TaskStates.edit_tags, lambda c: c.data.startswith("edit_tag_"))
async def edit_process_tags(callback: types.CallbackQuery, state: FSMContext):
    tag = callback.data.split("_")[2]
    data = await state.get_data()
    tags = data.get("tags", [])
    
    if tag in tags:
        tags.remove(tag)
    else:
        tags.append(tag)
    
    await state.update_data(tags=tags)
    await callback.answer(f"Тег '{tag}' {'добавлен' if tag in tags else 'удалён'}!")

@dp.callback_query(TaskStates.edit_tags, lambda c: c.data == "edit_done_tags")
async def edit_finish_tags(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TaskStates.edit_reminder)
    await callback.message.answer("⏰ Введите время в минутах до дедлайна для напоминания:")

@dp.message(TaskStates.edit_reminder)
async def edit_custom_reminder_handler(message: types.Message, state: FSMContext):
    try:
        minutes = int(message.text)
        if minutes <= 0:
            await message.answer("❌ Введите положительное число минут!")
            return

        data = await state.get_data()
        deadline = data["deadline"]
        reminder_time = deadline - timedelta(minutes=minutes)
        
        await state.update_data(reminder_time=reminder_time)
        await finalize_task_edit(message, state)
    except ValueError:
        await message.answer("❌ Введите целое число минут!")

async def finalize_task_edit(message: types.Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["edit_task_id"]
    
    update_data = {
        "description": data["description"],
        "deadline": data["deadline"],
        "tags": data.get("tags", []),
        "reminder_time": data["reminder_time"]
    }
    
    try:
        Task.update(task_id, update_data)
        celery_app.send_task(
            "tasks.send_reminder",
            args=(task_id,),
            eta=data["reminder_time"].astimezone(pytz.utc)
        )
        await message.answer("✅ Задача обновлена!", reply_markup=main_keyboard())
    except InvalidId:
        await message.answer("❌ Ошибка обновления задачи!")
    await state.clear()

if __name__ == "__main__":
    from mongodb import init_db
    init_db()
    dp.run_polling(bot)