from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return response

    request = context.get("request")
    view = context.get("view")
    detail = response.data.get("detail") if isinstance(response.data, dict) else None
    errors = response.data if isinstance(response.data, dict) else {"detail": response.data}
    response.data = {
        "success": False,
        "message": detail or "Request failed.",
        "errors": errors,
        "status_code": response.status_code,
        "request_id": getattr(request, "request_id", None),
        "view": view.__class__.__name__ if view else None,
    }
    return response
