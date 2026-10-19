"""使用不同的业务异常区分失败原因，在应用入口统一转换为 HTTP 响应。"""


class ServiceError(Exception):
    pass


class AuthenticationError(ServiceError):
    pass


class PermissionDeniedError(ServiceError):
    pass


class ServiceUnavailableError(ServiceError):
    pass


class ContentGenerationError(ServiceUnavailableError):
    """草稿结构校验失败，中断保存流程。"""


class NotFoundError(ServiceError):
    pass


class InvalidInputError(ServiceError):
    pass


class ConflictError(ServiceError):
    pass


class InvalidTransitionError(ConflictError):
    pass
