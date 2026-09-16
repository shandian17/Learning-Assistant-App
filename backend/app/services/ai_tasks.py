from concurrent.futures import ThreadPoolExecutor

from flask import current_app


_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="deepseek-task")


def enqueue_ai_task(task, *args) -> None:
    app = current_app._get_current_object()
    if app.config.get("PROCESS_AI_INLINE"):
        task(app, *args)
    else:
        _executor.submit(task, app, *args)
