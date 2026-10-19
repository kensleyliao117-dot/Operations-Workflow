from .exceptions import InvalidInputError


def validate_id(value: int) -> None:
    if value <= 0:
        raise InvalidInputError("ID 必须大于 0")


def validate_pagination(offset: int, limit: int) -> None:
    if offset < 0 or not 1 <= limit <= 100:
        raise InvalidInputError("offset 必须 >= 0，limit 必须在 1 到 100 之间")
