# AGENTS.md - Coding Guidelines & Best Practices

## Project Structure

```
VectorVK/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI application entry point
│   │   ├── database.py             # DB engine, sessions, table creation
│   │   ├── models.py               # SQLAlchemy ORM models
│   │   ├── dependencies.py          # FastAPI dependency injection
│   └── routers/                    # API route handlers
│       ├── __init__.py
│       ├── users.py                # User CRUD & auth
│       ├── admin.py                # Admin dashboard & settings
│       ├── classes.py              # Class management (teachers)
│       ├── assignments.py          # Assignment CRUD
│       ├── tasks.py                # Task management (students)
│       ├── grades.py               # Grades tracking
│       ├── schedule.py             # Schedule/weekly plan
│       ├── ai.py                   # AI chatbot integration
│       ├── notifications.py        # Notification system
│       └── yandex_auth.py          # Yandex login integration
├── frontend/                       # Static HTML/JS served at /static
│   ├── js/
│   │   ├── api.js                 # API client utilities
│   │   ├── auth-guard.js          # Auth redirect logic
│   │   ├── login.js               # Login handler
│   │   ├── register.js             # Registration handler
│   │   ├── verify.js               # Email verification handler
│   │   └── mobile-nav.js          # Mobile navigation
│   ├── admin/                     # Admin dashboard pages
│   ├── student/                   # Student portal pages
│   ├── teacher/                   # Teacher portal pages
│   ├── parent/                    # Parent portal pages
│   └── *.html                     # Auth & settings pages
├── docker-compose.yml             # Docker services configuration
├── requirements.txt               # Python dependencies
└── .env.example                    # Environment variables template
```

---

## Backend Coding Guidelines

### 1. General Principles

- **Async/Await Pattern**: All async endpoints must use `async def` with proper await usage
- **Dependency Injection**: Use FastAPI's dependency injection via `get_db()` from `database.py`
- **Type Hints**: Include type hints for all function parameters and return values
- **Error Handling**: Always handle exceptions gracefully with meaningful error messages
- **Validation**: Validate all inputs at the model/routing level, not just in endpoints

### 2. Database Models (models.py)

```python
# DO: Use SQLAlchemy declarative base with __tablename__
class MyModel(Base):
    __tablename__ = "my_table"
    
    id = Column(Integer, primary_key=True, index=True)
    # Add fields with appropriate types and constraints
    
    # Relationships with back_populates for bidirectional access
    related_model = relationship("OtherModel", back_populates="relation")

# DO: Use str, Enum for status/priority enums to avoid typos
class TaskStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"
    overdue = "overdue"

# DO: Always include timestamps (created_at, updated_at) with server_default=func.now()
# DON'T: Use datetime objects directly as column types
```

### 3. Routers (routers/*.py)

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db

router = APIRouter(prefix="/api", tags=["..."])

@router.get("/endpoint")
async def endpoint(item: Item, db: Session = Depends(get_db)):
    """
    Endpoint documentation.
    
    Args:
        item: Input validation
    
    Returns:
        Response data
    """
    try:
        # Business logic here
        return {"data": result}
    
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail="Database error")
```

### 4. User Authentication & Authorization

- **JWT Tokens**: Store in localStorage with `localStorage.setItem('token', token)`
- **User Data**: Also store user object for quick access: `localStorage.setItem('user', JSON.stringify(user))`
- **Auth Guard**: Use `requireAuth(allowed_roles)` helper from `api.js`
- **Password Hashing**: Use bcrypt or similar secure hashing (see `routers/users.py`)

### 5. API Response Format

```python
# Standard response format
{
    "data": {...},
    "message": "Optional success message"
}

# Error format
{
    "detail": "Error message"
}
```

---

## Frontend Coding Guidelines

### 1. JavaScript Patterns (js/*.js)

```javascript
// DO: Centralize API configuration in api.js
const API = window.location.origin;

// DO: Use consistent async patterns with try/catch
async function fetchData(path, options = {}) {
    const token = getToken();
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    
    const res = await fetch(API + path, { ...options, headers });
    if (res.status === 401) { logout(); return; }
    
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { detail: text }; }
    if (!res.ok) throw new Error(data.detail || 'Ошибка запроса');
    return data;
}

// DO: Use URLSearchParams for form submissions
async function submitForm(path, formData) {
    const token = getToken();
    const headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    
    const res = await fetch(API + path, {
        method: 'POST',
        headers,
        body: new URLSearchParams(formData).toString()
    });
    return res.json();
}

// DO: Use helper functions for data formatting
function formatDate(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleDateString('ru-RU', {
        day: '2-digit', month: '2-digit', year: 'numeric'
    });
}

// DON'T: Inline repetitive fetch logic
```

### 2. HTML Structure

- **Semantic HTML5** with proper heading hierarchy
- **Accessibility**: Use ARIA labels where needed, proper alt text for images
- **Form Validation**: Client-side validation before submission
- **Responsive Design**: Mobile-first CSS with flexbox/grid layouts

### 3. LocalStorage Management

```javascript
// Storage keys are fixed:
// - 'token': JWT authentication token
// - 'user': JSON stringified user object { role, name, ... }

function getToken() { return localStorage.getItem('token'); }
function getUser() { return JSON.parse(localStorage.getItem('user') || 'null'); }

function setAuth(token, user) {
  localStorage.setItem('token', token);
  localStorage.setItem('user', JSON.stringify(user));
}

// Clear both on logout
function logout() {
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  window.location.href = '/login.html';
}
```

---

## Best Practices

### Backend

1. **Database Migrations**: Use `database.py`'s `create_tables()` for schema updates
2. **Admin User**: Auto-create admin user on startup via `_ensure_admin_user()`
3. **CORS Configuration**: Configure in `main.py` with appropriate origins
4. **Static Files**: Mount frontend at `/static` using `StaticFiles`

### PostgreSQL & psycopg3 Guidelines

1. **Connection Pooling**: Use SQLAlchemy's connection pooling via `create_engine()` - configure `pool_size`, `max_overflow`, and `pool_pre_ping=True` for production to prevent stale connections
2. **Parameterized Queries**: Always use parameterized queries (SQLAlchemy handles this automatically) to prevent SQL injection
3. **Transaction Management**: Wrap multi-statement operations in explicit transactions; commit ASAP
4. **Connection Timeout**: Set `connect_args={"timeout": 10}` in production to prevent hanging on failed connections
5. **Bulk Operations**: Use `executemany()` or SQLAlchemy's bulk insert patterns for large datasets (e.g., mass notifications, grades)
6. **Index Strategy**: Add indexes on frequently queried columns (`email`, `created_at`, foreign keys); PostgreSQL optimizes better than SQLite with proper indexing
7. **JSONB Usage**: Store complex nested data (like AI message metadata or notification payloads) in JSONB columns for flexible querying
8. **Connection Health**: Use `pool_pre_ping=True` to validate connections before use; monitor connection pool size to avoid exhaustion
9. **Timezone Handling**: Always use `DateTime(timezone=True)` with UTC internally; convert to local time only at display layer
10. **Query Optimization**: Use `.options(selectinload())` for relationships to prevent N+1 queries; leverage PostgreSQL's advanced query features (CTEs, window functions)

### Frontend

1. **Error Handling**: All API calls must handle 401 (auth) and generic errors
2. **User Experience**: Show user-friendly error messages, never raw exceptions
3. **Loading States**: Disable buttons during async operations
4. **Localization**: Use Russian language for all UI text (`ru-RU` locale)

### Security

1. **Passwords**: Always hash with bcrypt before storing
2. **Email Verification**: Require email verification before account activation
3. **Token Expiry**: Implement token refresh logic
4. **Input Sanitization**: Escape all user inputs to prevent XSS

---

## Testing Guidelines

- **Backend**: Use pytest with FastAPI test client (`test_client(app)`)
- **Frontend**: Manual testing with browser DevTools for HTML/JS
- **Integration**: Test Docker containers together via docker-compose

---

## Development Workflow

1. **Clone & Setup**: `git clone`, copy `env.example` to `.env`, install deps
2. **Backend**: `uvicorn app.main:app --reload`
3. **Frontend**: Start static server or serve from backend directly
4. **Docker**: `docker-compose up --build` for containerized development

---

## Style Guide

### Python (Backend)
- PEP 8 formatting
- 79 character line limit
- Type hints for all functions and parameters
- Docstrings for public functions/classes
- Import sorting with `isort`

### JavaScript (Frontend)
- ES6+ syntax with arrow functions
- Functional programming patterns where appropriate
- Consistent naming conventions (`camelCase` for variables/functions)
- No external libraries beyond vanilla JS

---

## File Naming Conventions

- Backend: `snake_case` for Python files (`main.py`, `database.py`)
- Frontend: `kebab-case` for folders (`student/`, `teacher/`), `*.js` and `*.html`
- Routers: Organized by domain (users, admin, classes, etc.)

---

## Environment Variables (.env.example)

```
ADMIN_EMAIL=admin@vkcollege.ru
ADMIN_PASSWORD=secure_password_here
ADMIN_FIRST_NAME=Admin
ADMIN_LAST_NAME=User

DATABASE_PATH=/home/user/.vector/vk.db

# CORS origins as needed for your deployment
```

---

**Last Updated**: 2026-10-17

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
