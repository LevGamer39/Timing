import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config
from app.database import create_tables
from app.routers import users, admin, classes, assignments, tasks, grades, schedule, ai, notifications, yandex_auth
from app.routers.notifications import start_scheduler, stop_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()

load_dotenv()

app = FastAPI(title="Vector API", lifespan=lifespan)

current_dir = os.path.dirname(os.path.abspath(__file__))
frontend_path = os.path.abspath(os.path.join(current_dir, "../../frontend/"))
templates = Jinja2Templates(directory=os.path.join(frontend_path, "templates/"))

if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=os.path.join(frontend_path, "static/")), name="static")

# Явный белый список: "*" вместе с allow_credentials=True браузеры не
# принимают, а прежний список литералов собирался, но никуда не передавался.
# Продовый адрес берётся из APP_URL в .env, поэтому смена домена не требует
# правки кода; локальные адреса нужны для разработки без домена.
CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8000",
    "http://localhost:8080",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8080",
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
app.include_router(ai.router)
app.include_router(notifications.router)
app.include_router(yandex_auth.router)

def serve(relative_path: str, request: Request, context: dict[str,any] | None = None) -> FileResponse:
    """Отдаёт страницу из frontend/. Context

    Отсутствующие файлы дают 404. Раньше FileResponse возвращался без
    проверки, поэтому любой маршрут, указывающий на несуществующий файл
    (student/profile.html, parent/*.html), давал 500.
    """
    # Python creates default objects once so if one function call edits the default value it will apply to all further function calls
    if context is None: context = {}
    target = os.path.join(frontend_path, "templates", relative_path)
    if not os.path.isfile(target):
        raise HTTPException(
            status_code=404, detail=f"Page not found: {relative_path}"
        )
    return templates.TemplateResponse(request=request, name=relative_path, context=context)

# Публичные страницы
@app.get("/")
async def index(r: Request):
    return serve("index.html", r)

@app.get("/login")
async def login_page(r: Request):
    return serve("login.html", r)

@app.get("/register")
async def register_page(r: Request):
    return serve("register.html", r)

@app.get("/verify")
async def verify_page(r: Request):
    return serve("verify.html", r)

@app.get("/reset-password")
async def reset_password_page(r: Request):
    return serve("reset-password.html", r)

@app.get("/reset-password-new")
async def reset_password_new_page(r: Request):
    return serve("reset-password-new.html", r)


# Страницы ученика
@app.get("/dashboard")
@app.get("/student/dashboard")
async def student_dashboard(r: Request):
    return serve("student/dashboard.html", r)

@app.get("/student/tasks")
async def student_tasks(r: Request):
    return serve("student/tasks.html", r)

@app.get("/student/calendar")
async def student_calendar(r: Request):
    return serve("student/calendar.html", r)

@app.get("/student/progress")
async def student_progress(r: Request):
    return serve("student/progress.html", r)

@app.get("/student/ai")
async def student_ai(r: Request):
    return serve("student/ai.html", r)

@app.get("/student/profile")
async def student_profile(r: Request):
    return serve("student/profile.html", r)


# Страницы учителя
@app.get("/teacher/dashboard")
async def teacher_dashboard(r: Request):
    return serve("teacher/dashboard.html", r)

@app.get("/teacher/classes")
async def teacher_classes(r: Request):
    return serve("teacher/classes.html", r)

@app.get("/teacher/classes/{class_id}")
async def teacher_class(r: Request, class_id: int):
    return serve("teacher/class.html", r)

@app.get("/teacher/assignments")
async def teacher_assignments(r: Request):
    return serve("teacher/assignments.html", r)

@app.get("/teacher/assignments/new")
async def teacher_assignment_new(r: Request):
    return serve("teacher/assignment-new.html", r)

@app.get("/teacher/profile")
async def teacher_profile(r: Request):
    return serve("teacher/profile.html", r)

@app.get("/teacher/settings")
async def teacher_settings(r: Request):
    return serve("teacher/settings.html", r)


# Страницы админа
@app.get("/admin")
@app.get("/admin/dashboard")
async def admin_dashboard(r: Request):
    return serve("admin/dashboard.html", r)

@app.get("/admin/settings")
async def admin_settings(r: Request):
    return serve("admin/settings.html", r)


# Страницы родителя
@app.get("/parent/dashboard")
async def parent_dashboard(r: Request):
    return serve("parent/dashboard.html", r)

@app.get("/parent/child/{student_id}")
async def parent_child(r: Request, student_id: int):
    return serve("parent/child.html", r)

@app.get("/parent/notifications")
async def parent_notifications(r: Request):
    return serve("parent/notifications.html", r)

@app.get("/parent/profile")
async def parent_profile(r: Request):
    return serve("parent/profile.html", r)


# Общие страницы
@app.get("/settings")
async def settings_page(r: Request):
    return serve("settings.html", r)

@app.get("/student/settings")
async def student_settings_page(r: Request):
    return serve("settings.html", r)

@app.get("/settings/change-email")
async def change_email_page(r: Request):
    return serve("change-email.html", r)

@app.get("/settings/change-email/confirm")
async def change_email_confirm_page(r: Request):
    return serve("change-email-confirm.html", r)

@app.get("/settings/change-password")
async def change_password_page(r: Request):
    return serve("change-password.html", r)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.exception_handler(404)
async def page_not_found(request: Request, exc: Exception):
    path_404 = os.path.join(frontend_path, "404.html")
    if os.path.exists(path_404):
        return FileResponse(path_404, status_code=404)
    return HTMLResponse("<h1>404</h1>", status_code=404)
