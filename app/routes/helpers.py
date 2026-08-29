from fastapi import Request


def services(request: Request): return request.app.state.services


def templates(request: Request): return request.app.state.templates


def redirect(path: str):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(path, status_code=303)
