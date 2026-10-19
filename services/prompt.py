from models.prompt import Prompt
from models.user import Users
from schemas.prompt import PromptCreate, PromptUpdate
from .common import current_user_id, get_record, list_records, write_transaction
from .common import remove_record


def create_prompt(db, data: PromptCreate) -> Prompt:
    with write_transaction(db):
        get_record(db, Users, current_user_id(db))
        prompt = Prompt(**data.model_dump(), user_id=current_user_id(db), version=1)
        db.add(prompt)
    return prompt


def update_prompt(db, prompt_id: int, data: PromptUpdate) -> Prompt:
    with write_transaction(db):
        prompt = get_record(db, Prompt, prompt_id, lock=True)
        changed = any(getattr(prompt, key) != value for key, value in data.model_dump().items())
        if changed:
            for key, value in data.model_dump().items():
                setattr(prompt, key, value)
            prompt.version += 1
    return prompt


def get_prompt(db, prompt_id):
    return get_record(db, Prompt, prompt_id)


def delete_prompt(db, prompt_id):
    remove_record(db, Prompt, prompt_id)


def list_prompts(db, *, user_id=None, platform=None, is_active=None, offset=0, limit=20):
    return list_records(db, Prompt, user_id=user_id, platform=platform, is_active=is_active,
                        offset=offset, limit=limit)
