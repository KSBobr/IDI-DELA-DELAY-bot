from datetime import datetime, timedelta
from bson import ObjectId
from config import db
import pytz

def init_db():
    db.tasks.create_index([("status", 1)])
    db.tasks.create_index([("user_id", 1), ("deadline", 1)])
    db.users.create_index([("user_id", 1)], unique=True)

class User:
    @staticmethod
    def get_or_create(user_id: int):
        return db.users.find_one_and_update(
            {"user_id": user_id},
            {"$setOnInsert": {"tags": [], "created_at": datetime.now()}},
            upsert=True,
            return_document=True
        )

    @staticmethod
    def add_tag(user_id: int, tag: str):
        return db.users.update_one(
            {"user_id": user_id},
            {"$addToSet": {"tags": tag}},
            upsert=True
        )

    @staticmethod
    def remove_tag(user_id: int, tag: str):
        return db.users.update_one(
            {"user_id": user_id},
            {"$pull": {"tags": tag}}
        )

class Task:
    @staticmethod
    def create(task_data: dict):
        return db.tasks.insert_one(task_data)

    @staticmethod
    def get_active_tasks(user_id: int):
        return list(db.tasks.find({
            "user_id": user_id,
            "status": {"$in": ["active", "overdue"]}
        }))

    @staticmethod
    def get_task(task_id: str):
        return db.tasks.find_one({"_id": ObjectId(task_id)})

    @staticmethod
    def update(task_id: str, update_data: dict):
        return db.tasks.update_one(
            {"_id": ObjectId(task_id)},
            {"$set": update_data}
        )

    @staticmethod
    def delete(task_id: str):
        return db.tasks.delete_one({"_id": ObjectId(task_id)})

    @staticmethod
    def delete_by_date(user_id: int, date: datetime):
        start = datetime(date.year, date.month, date.day, tzinfo=pytz.utc)
        end = start + timedelta(days=1)
        return db.tasks.delete_many({
            "user_id": user_id,
            "deadline": {"$gte": start, "$lt": end}
        })

    @staticmethod
    def delete_all(user_id: int):
        return db.tasks.delete_many({"user_id": user_id})

    @staticmethod
    def get_tasks_by_tag(user_id: int, tag: str):
        return list(db.tasks.find({
            "user_id": user_id,
            "tags": tag,
            "status": {"$in": ["active", "overdue"]}
        }))

    @staticmethod
    def remove_tag_from_all_tasks(user_id: int, tag: str):
        return db.tasks.update_many(
            {"user_id": user_id},
            {"$pull": {"tags": tag}}
        )