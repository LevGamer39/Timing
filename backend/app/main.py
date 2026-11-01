import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.database import create_tables
from app.routers import (
    users,
    admin,
    classes,
    assignments,
    tasks,
    grades,
    schedule,
    journal,
    attendance,
    messages,
    school,
    ai,
    notifications,
    yandex_auth,
)
from app.routers.notifications import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(title="Vector API", lifespan=lifespan)

current_dir = os.path.dirname(os.path.abspath(__file__))
frontend_path = os.path.abspath(os.path.join(current_dir, "../../frontend"))

if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")

# Явный белый список: "*" вместе с allow_credentials=True браузеры не
# принимают, а прежний список литералов собирался, но никуда не передавался.
# Продовый адрес берётся из APP_URL в .env, поэтому смена домена не требует
# правки кода; локальные адреса нужны для разработки без домена.
CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8000",
    "http://localhost:8080",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8080",
    "http://127.0.0.1:5173",
]
if config.APP_URL:
    CORS_ORIGINS.append(config.APP_URL.rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(users.router)
app.include_router(admin.router)
app.include_router(classes.router)
app.include_router(assignments.router)
app.include_router(tasks.router)
app.include_router(grades.router)
app.include_router(schedule.router)
app.include_router(journal.router)
app.include_router(attendance.router)
app.include_router(messages.router)
app.include_router(school.router)
app.include_router(ai.router)
app.include_router(notifications.router)
app.include_router(yandex_auth.router)

def serve(relative_path: str) -> FileResponse:
    """Отдаёт страницу из frontend/.

    Приложение — одна оболочка `index.html`, остальные экраны живут в хеше
    (`#/home`, `#/diary`). Поэтому старые адреса (`/login`, `/dashboard`,
    `/teacher/*` и т.п.) отдают оболочку: она сама решает, куда перейти.
    Прямой 404 остался бы только для отсутствующего `index.html`.
    """
    target = os.path.join(frontend_path, relative_path)
    if not os.path.isfile(target):
        target = os.path.join(frontend_path, "index.html")
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="Frontend is not built")
    return FileResponse(target)


# Публичные страницы
@app.get("/")
async def index():
    return serve("index.html")

@app.get("/login")
async def login_page():
    return serve("login.html")

@app.get("/register")
async def register_page():
    return serve("register.html")

@app.get("/verify")
async def verify_page():
    return serve("verify.html")

@app.get("/reset-password")
async def reset_password_page():
    return serve("reset-password.html")

@app.get("/reset-password-new")
async def reset_password_new_page():
    return serve("reset-password-new.html")


# Страницы ученика
@app.get("/dashboard")
@app.get("/student/dashboard")
async def student_dashboard():
    return serve("student/dashboard.html")

@app.get("/student/tasks")
async def student_tasks():
    return serve("student/tasks.html")

@app.get("/student/calendar")
async def student_calendar():
    return serve("student/calendar.html")

@app.get("/student/progress")
async def student_progress():
    return serve("student/progress.html")

@app.get("/student/ai")
async def student_ai():
    return serve("student/ai.html")

@app.get("/student/profile")
async def student_profile():
    return serve("student/profile.html")


# Страницы учителя
@app.get("/teacher/dashboard")
async def teacher_dashboard():
    return serve("teacher/dashboard.html")

@app.get("/teacher/classes")
async def teacher_classes():
    return serve("teacher/classes.html")

@app.get("/teacher/classes/{class_id}")
async def teacher_class(class_id: int):
    return serve("teacher/class.html")

@app.get("/teacher/assignments")
async def teacher_assignments():
    return serve("teacher/assignments.html")

@app.get("/teacher/assignments/new")
async def teacher_assignment_new():
    return serve("teacher/assignment-new.html")

@app.get("/teacher/profile")
async def teacher_profile():
    return serve("teacher/profile.html")

@app.get("/teacher/settings")
async def teacher_settings():
    return serve("teacher/settings.html")


# Страницы админа
@app.get("/admin")
@app.get("/admin/dashboard")
async def admin_dashboard():
    return serve("admin/dashboard.html")

@app.get("/admin/settings")
async def admin_settings():
    return serve("admin/settings.html")


# Страницы родителя
@app.get("/parent/dashboard")
async def parent_dashboard():
    return serve("parent/dashboard.html")

@app.get("/parent/child/{student_id}")
async def parent_child(student_id: int):
    return serve("parent/child.html")

@app.get("/parent/notifications")
async def parent_notifications():
    return serve("parent/notifications.html")

@app.get("/parent/profile")
async def parent_profile():
    return serve("parent/profile.html")


# Общие страницы
@app.get("/settings")
async def settings_page():
    return serve("settings.html")

@app.get("/student/settings")
async def student_settings_page():
    return serve("settings.html")

@app.get("/settings/change-email")
async def change_email_page():
    return serve("change-email.html")

@app.get("/settings/change-email/confirm")
async def change_email_confirm_page():
    return serve("change-email-confirm.html")

@app.get("/settings/change-password")
async def change_password_page():
    return serve("change-password.html")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.exception_handler(404)
async def page_not_found(request: Request, exc: Exception):
    # API всегда отвечает JSON: бот и SPA не умеют разбирать HTML-страницу
    # ошибки, а глобальный 404-хендлер раньше подменял detail на HTML.
    if request.url.path.startswith("/api/"):
        detail = getattr(exc, "detail", "Not found")
        return JSONResponse(status_code=404, content={"detail": detail})

    path_404 = os.path.join(frontend_path, "404.html")
    if os.path.exists(path_404):
        return FileResponse(path_404, status_code=404)
    return HTMLResponse("<h1>404</h1>", status_code=404)


# SPA: css/, js/, html/ лежат рядом с index.html и запрашиваются с корня.
# Монтируется последним: иначе Mount("/") перехватил бы страницы и /health.
if os.path.exists(frontend_path):
    app.mount(
        "/",
        StaticFiles(directory=frontend_path, html=True),
        name="frontend",
    )
