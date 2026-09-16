from http import HTTPStatus

from flask import Flask, jsonify, request


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    def response(self):
        return jsonify({"error": {"code": self.code, "message": self.message, "details": self.details}}), self.status


def not_implemented(feature: str):
    raise APIError(HTTPStatus.NOT_IMPLEMENTED, "NOT_IMPLEMENTED", f"{feature}接口尚未实现")


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(APIError)
    def handle_api_error(error: APIError):
        return error.response()

    @app.errorhandler(404)
    def handle_not_found(_error):
        if request.path.startswith("/api/"):
            return APIError(404, "NOT_FOUND", "接口或资源不存在").response()
        return "Not Found", 404

    @app.errorhandler(405)
    def handle_method_not_allowed(_error):
        if request.path.startswith("/api/"):
            return APIError(405, "METHOD_NOT_ALLOWED", "请求方法不允许").response()
        return "Method Not Allowed", 405

    @app.errorhandler(413)
    def handle_too_large(_error):
        return APIError(413, "FILE_TOO_LARGE", "上传文件超过大小限制").response()

    @app.errorhandler(500)
    def handle_internal_error(error):
        app.logger.error("Unhandled server error", exc_info=(type(error), error, error.__traceback__))
        return APIError(500, "INTERNAL_ERROR", "服务器内部错误").response()
