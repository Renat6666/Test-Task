# Task Management API

Асинхронний REST API для управління задачами: JWT-аутентифікація, CRUD задач з
бізнес-правилами, машина статусів, комментарі з rate limiting на Redis,
інтеграція з публічним API цитат через Celery, пошук/фільтри/сортування,
аналітика з кешем, CSV-експорт і логування повільних запитів.

## Стек

Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) + asyncpg · PostgreSQL 16 ·
Alembic · Redis 7 · Celery · Pydantic v2 · HTTPX · pytest + pytest-asyncio ·
Docker Compose

## Запуск

```bash
cp .env.example .env     # за потреби змініть JWT_SECRET і порти
docker compose up --build
```

Піднімається чотири сервіси: `api`, `worker` (Celery), `postgres`, `redis`.
Окремого `Dockerfile` немає — усе описано в `docker-compose.yml`: `api` і `worker`
беруть готовий образ `python:3.12-slim` і встановлюють залежності при старті
(pip-кеш у named volume, тому повторні запуски швидкі). Міграції Alembic
застосовуються автоматично перед старом веб-сервера.

- API: http://localhost:8000
- Swagger: http://localhost:8000/docs
- Health: http://localhost:8000/health

Порти: API `8000`, PostgreSQL `5432`, Redis `6380` на хості (всередині мережі
compose — стандартний `6379`; зовнішній порт зміщений, бо на хості вже працює
системний сервіс `redis-server`). Перший старт довший — іде встановлення
залежностей; далі контейнери підіймаються за секунди.

## Тести

```bash
# у контейнері
docker compose exec api python -m pytest -q

# з хоста (використовує Postgres і Redis з compose)
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/tasks_test \
TEST_REDIS_URL=redis://127.0.0.1:6380/15 \
python -m pytest -q
```

102 тести: аутентифікація, переходи статусів, лімит активних задач,
rate limiting комментарів, middleware, фільтрація і генерація OpenAPI.

### CI

`.github/workflows/tests.yml` — тести проганяються автоматично на кожен push у
будь-яку гілку, а також на pull request (і вручну через «Run workflow»).
Повторний push у ту саму гілку скасовує попередній незавершений прогін.

Крокі job-а: підняття сервісів PostgreSQL 16 і Redis 7 → встановлення
залежностей (з кешем pip) → створення тестової БД → `alembic upgrade head` →
`alembic check` (міграції мусять відповідати моделям) → `pytest -q`.

## Ендпоінти

| Метод | Шлях | Опис |
|---|---|---|
| POST | `/auth/register` | реєстрація |
| POST | `/auth/login` | логін, пара JWT |
| POST | `/auth/refresh` | оновлення токенів |
| GET | `/auth/me` | поточний користувач |
| GET | `/users` | довідник користувачів |
| GET | `/tasks` | список: пошук, фільтри, сортування, пагінація |
| POST | `/tasks` | створення |
| GET | `/tasks/{id}` | деталі |
| PATCH | `/tasks/{id}` | редагування (крім статусу) |
| DELETE | `/tasks/{id}` | видалення |
| PATCH | `/tasks/{id}/status` | зміна статусу |
| GET | `/tasks/{id}/history` | історія переходів |
| GET | `/tasks/analytics` | аналітика (кеш Redis 60 с) |
| GET | `/tasks/export` | CSV-експорт |
| GET | `/tasks/{id}/comments` | комментарі задачі |
| POST | `/tasks/{id}/comments` | створення комментаря (5/хв) |

Усі ендпоінти задач і комментарів вимагають заголовок `Authorization: Bearer <access_token>`.

## Структура

```
core/       конфіг, безпека, залежності, middleware, rate limiting, логування
db/         SQLAlchemy-моделі та сесії
services/   по сервісу на домен: schemas.py (Pydantic) · views.py (логіка) · controllers.py (роути)
workers/    Celery-застосунок і фонові задачі
alembic/    міграції
tests/      pytest
```

Поділ усередині сервісу: **`schemas.py`** — лише Pydantic-серіалізація,
**`views.py`** — увесь функціонал ендпоінтів, **`controllers.py`** — лише
оголошення роутів (`APIRouter` + `add_api_route`), які делегують у `views.py`.

## Формат помилок

```json
{"detail": {"code": "ACTIVE_TASK_LIMIT_EXCEEDED", "message": "User 1 already has 10 active tasks (limit is 10)"}}
```

Коди бізнес-помилок: `ACTIVE_TASK_LIMIT_EXCEEDED`, `TASK_IS_FINAL`,
`ASSIGNEE_CHANGE_LOCKED`, `TASK_DELETE_FORBIDDEN`, `INVALID_STATUS_TRANSITION`,
`DONE_REQUIRES_ASSIGNEE`, `DONE_DEADLINE_PASSED`, `RATE_LIMIT_EXCEEDED`.
