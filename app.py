from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import Flask, g, jsonify, request, send_from_directory, session


ROOT = Path(__file__).resolve().parent
PASSWORD_ITERATIONS = 600_000
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{3,80}$")
ROLES = {"employee": "Сотрудник", "manager": "Руководитель отдела", "admin": "Администратор"}
DEPARTMENT_NAMES = (
    "ОИИЗ — Отдел инженерных изысканий",
    "ОПЖД — Отдел проектирования железных дорог",
    "АСО — Архитектурно-строительный отдел",
    "АТиС — Отдел автоматики, телемеханики и связи",
    "ИССО — Отдел искусственных сооружений",
    "ЭТО — Отдел электротехники и контактной сети",
    "ОСР — Отдел сметных работ",
    "ОСИТ — Отдел современных информационных технологий и выпуска проектов",
)
PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 256
POSITION_NAME_MAX_LENGTH = 300
LEGACY_DEPARTMENT_NAMES = (
    ("Отдел геологии и изысканий", DEPARTMENT_NAMES[0]),
    ("Отдел проектирования железных дорог", DEPARTMENT_NAMES[1]),
    ("Архитектурно-строительный отдел", DEPARTMENT_NAMES[2]),
    ("Отдел СЦБ и связи", DEPARTMENT_NAMES[3]),
    ("Отдел технических сооружений", DEPARTMENT_NAMES[4]),
    ("Отдел электротехники и контактной сети", DEPARTMENT_NAMES[5]),
    ("Отдел проектирования автомобильных дорог", DEPARTMENT_NAMES[6]),
    ("Отдел современных информационных технологий", DEPARTMENT_NAMES[7]),
)


def database_path() -> Path:
    path = Path(os.environ.get("DATABASE_PATH", "data/workload.sqlite3"))
    return path if path.is_absolute() else ROOT / path


def connect_database() -> sqlite3.Connection:
    connection = sqlite3.connect(database_path(), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 15000")
    return connection


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    actual_salt = salt or secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), actual_salt, PASSWORD_ITERATIONS
    )
    return actual_salt, password_hash


def password_matches(password: str, salt: bytes, expected_hash: bytes) -> bool:
    _, actual_hash = hash_password(password, salt)
    return hmac.compare_digest(actual_hash, expected_hash)


def valid_user_password(password: Any) -> bool:
    return (
        isinstance(password, str)
        and PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH
        and len(re.findall(r"[A-Z]", password)) >= 2
        and len(re.findall(r"[a-z]", password)) >= 2
    )


def initialize_database() -> None:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect_database()
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS departments (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                department_id INTEGER NOT NULL REFERENCES departments(id),
                position_id INTEGER NOT NULL REFERENCES positions(id),
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                UNIQUE(name, department_id)
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('employee', 'manager', 'admin')),
                employee_id INTEGER REFERENCES employees(id),
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                auth_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS works (
                id INTEGER PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id),
                created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                department_id INTEGER REFERENCES departments(id),
                date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                title TEXT NOT NULL,
                employee_name TEXT NOT NULL,
                department_name TEXT NOT NULL,
                position_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_works_date ON works(date);
            CREATE INDEX IF NOT EXISTS ix_works_department_date ON works(department_id, date);
            CREATE INDEX IF NOT EXISTS ix_works_employee_date ON works(employee_id, date);
            CREATE TABLE IF NOT EXISTS login_attempts (
                address TEXT PRIMARY KEY,
                failed_count INTEGER NOT NULL,
                last_failure TEXT NOT NULL
            );
            """
        )
        for old_name, new_name in LEGACY_DEPARTMENT_NAMES:
            connection.execute(
                "UPDATE departments SET name = ? WHERE name = ?", (new_name, old_name)
            )
            connection.execute(
                "UPDATE works SET department_name = ? WHERE department_name = ?",
                (new_name, old_name),
            )
        for name in DEPARTMENT_NAMES:
            connection.execute("INSERT OR IGNORE INTO departments(name) VALUES (?)", (name,))
        for name in (
            "Начальник отдела",
            "Главный специалист",
            "Ведущий инженер",
            "Инженер 1 категории",
            "Инженер 2 категории",
            "Инженер 3 категории",
            "Техник",
        ):
            connection.execute("INSERT OR IGNORE INTO positions(name) VALUES (?)", (name,))

        admin_username = os.environ.get("ADMIN_USERNAME", "").strip()
        admin_password = os.environ.get("ADMIN_PASSWORD", "")
        if admin_username and admin_password:
            if not USERNAME_PATTERN.fullmatch(admin_username):
                raise RuntimeError("ADMIN_USERNAME must contain 3-80 letters, digits, or . _ @ -")
            if len(admin_password) < 12 or admin_password == "replace-with-a-unique-strong-password":
                raise RuntimeError("ADMIN_PASSWORD must contain at least 12 characters")
            if not connection.execute(
                "SELECT 1 FROM users WHERE username = ?", (admin_username,)
            ).fetchone():
                salt, password_hash = hash_password(admin_password)
                connection.execute(
                    """INSERT INTO users
                       (username, password_salt, password_hash, role, created_at)
                       VALUES (?, ?, ?, 'admin', ?)""",
                    (admin_username, salt, password_hash, utc_now()),
                )
        connection.commit()
    finally:
        connection.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_error(message: str, status: int = 400):
    return jsonify(error=message), status


def get_database() -> sqlite3.Connection:
    if "database" not in g:
        g.database = connect_database()
    return g.database


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = get_database().execute(
        """SELECT u.id, u.username, u.role, u.employee_id, u.active, u.auth_version,
                  e.name AS employee_name, e.department_id, d.name AS department_name
           FROM users u
           LEFT JOIN employees e ON e.id = u.employee_id
           LEFT JOIN departments d ON d.id = e.department_id
           WHERE u.id = ?""",
        (user_id,),
    ).fetchone()
    if (
        user is None
        or not user["active"]
        or user["auth_version"] != session.get("auth_version")
    ):
        session.clear()
        return None
    return user


def login_required(handler: Callable):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return json_error("Требуется войти в систему.", 401)
        g.user = user
        return handler(*args, **kwargs)

    return wrapped


def roles_required(*roles: str):
    def decorate(handler: Callable):
        @wraps(handler)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] not in roles:
                return json_error("Недостаточно прав для выполнения операции.", 403)
            return handler(*args, **kwargs)

        return wrapped

    return decorate


def parse_json_object():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None
    return payload


def validate_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
        return len(value) == 10
    except ValueError:
        return False


def employee_rows(connection: sqlite3.Connection, user) -> list[dict]:
    sql = """SELECT e.id, e.name, e.department_id, d.name AS department,
                    e.position_id, p.name AS position, e.active
             FROM employees e
             JOIN departments d ON d.id = e.department_id
             JOIN positions p ON p.id = e.position_id
             WHERE e.active = 1"""
    params: tuple[Any, ...] = ()
    if user["role"] == "employee":
        sql += " AND e.id = ?"
        params = (user["employee_id"],)
    elif user["role"] == "manager":
        sql += " AND e.department_id = ?"
        params = (user["department_id"],)
    sql += " ORDER BY e.name COLLATE NOCASE"
    return [dict(row) for row in connection.execute(sql, params)]


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static")
    secret_key = os.environ.get("SECRET_KEY", "")
    if len(secret_key) < 32 or secret_key == "replace-with-at-least-32-random-bytes":
        raise RuntimeError("Set a unique SECRET_KEY of at least 32 characters before starting.")
    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(
            hours=max(1, int(os.environ.get("SESSION_HOURS", "8")))
        ),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    initialize_database()
    connection = connect_database()
    try:
        if not connection.execute(
            "SELECT 1 FROM users WHERE role = 'admin' AND active = 1"
        ).fetchone():
            raise RuntimeError(
                "No active administrator found. Set ADMIN_USERNAME and "
                "ADMIN_PASSWORD (at least 12 characters) in .env before first launch."
            )
    finally:
        connection.close()

    @app.before_request
    def protect_api():
        if request.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            expected = session.get("csrf_token")
            provided = request.headers.get("X-CSRF-Token", "")
            if not expected or not hmac.compare_digest(expected, provided):
                return json_error("Сессия истекла. Обновите страницу и войдите снова.", 400)

    @app.after_request
    def secure_response(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'self'"
        )
        return response

    @app.teardown_appcontext
    def close_database(_error=None):
        connection = g.pop("database", None)
        if connection is not None:
            connection.close()

    @app.get("/")
    def index():
        return send_from_directory(ROOT, "index.html")

    @app.get("/healthz")
    def health():
        try:
            get_database().execute("SELECT 1 FROM users LIMIT 1").fetchone()
        except sqlite3.Error:
            app.logger.exception("Database health check failed")
            return jsonify(status="unavailable"), 503
        return jsonify(status="ok")

    @app.get("/api/me")
    def get_me():
        user = current_user()
        if not session.get("csrf_token"):
            session["csrf_token"] = secrets.token_urlsafe(32)
        if user is None:
            return jsonify(authenticated=False, csrf_token=session["csrf_token"])
        session.permanent = True
        return jsonify(
            authenticated=True,
            csrf_token=session["csrf_token"],
            user={
                "id": user["id"],
                "username": user["username"],
                "role": user["role"],
                "role_name": ROLES[user["role"]],
                "employee_id": user["employee_id"],
                "employee_name": user["employee_name"],
                "department_id": user["department_id"],
                "department_name": user["department_name"],
            },
        )

    @app.post("/api/login")
    def login():
        payload = parse_json_object()
        if payload is None:
            return json_error("Проверьте логин и пароль.")
        username = str(payload.get("username", "")).strip()
        password = payload.get("password", "")
        if not isinstance(password, str) or not username:
            return json_error("Проверьте логин и пароль.", 401)

        connection = get_database()
        address = f"{request.remote_addr or 'unknown'}:{username.casefold()}"
        attempt = connection.execute(
            "SELECT failed_count, last_failure FROM login_attempts WHERE address = ?",
            (address,),
        ).fetchone()
        if attempt and attempt["failed_count"] >= 10:
            try:
                last_failure = datetime.fromisoformat(attempt["last_failure"])
            except ValueError:
                last_failure = datetime.now(timezone.utc)
            if datetime.now(timezone.utc) - last_failure < timedelta(minutes=15):
                return json_error("Слишком много попыток входа. Подождите 15 минут.", 429)

        user = connection.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        valid = (
            user is not None
            and bool(user["active"])
            and password_matches(password, user["password_salt"], user["password_hash"])
        )
        if not valid:
            count = attempt["failed_count"] + 1 if attempt else 1
            connection.execute(
                """INSERT INTO login_attempts(address, failed_count, last_failure)
                   VALUES (?, ?, ?)
                   ON CONFLICT(address) DO UPDATE SET
                     failed_count = excluded.failed_count,
                     last_failure = excluded.last_failure""",
                (address, count, utc_now()),
            )
            connection.commit()
            return json_error("Неверный логин или пароль.", 401)

        connection.execute("DELETE FROM login_attempts WHERE address = ?", (address,))
        connection.commit()
        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        session["auth_version"] = user["auth_version"]
        session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify(ok=True)

    @app.post("/api/logout")
    @login_required
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.post("/api/password")
    @login_required
    def change_password():
        payload = parse_json_object()
        if payload is None:
            return json_error("Проверьте введённые данные.")
        old_password = payload.get("old_password", "")
        new_password = payload.get("new_password", "")
        if not isinstance(old_password, str):
            return json_error("Текущий пароль указан неверно.", 401)
        stored = get_database().execute(
            "SELECT password_salt, password_hash FROM users WHERE id = ?",
            (g.user["id"],),
        ).fetchone()
        if not password_matches(
            old_password, stored["password_salt"], stored["password_hash"]
        ):
            return json_error("Текущий пароль указан неверно.", 401)
        if not valid_user_password(new_password):
            return json_error(
                "Пароль должен содержать минимум 10 символов, 2 заглавные и 2 строчные латинские буквы."
            )
        salt, password_hash = hash_password(new_password)
        connection = get_database()
        connection.execute(
            """UPDATE users
               SET password_salt = ?, password_hash = ?, auth_version = auth_version + 1
               WHERE id = ?""",
            (salt, password_hash, g.user["id"]),
        )
        connection.commit()
        session["auth_version"] += 1
        session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify(ok=True, csrf_token=session["csrf_token"])

    @app.get("/api/data")
    @login_required
    def get_data():
        connection = get_database()
        user = g.user
        departments_query = """SELECT d.id, d.name, COUNT(e.id) AS employee_count
                               FROM departments d
                               LEFT JOIN employees e
                                 ON e.department_id = d.id AND e.active = 1
                               WHERE d.active = 1"""
        department_params: tuple[Any, ...] = ()
        if user["role"] == "employee":
            departments_query += " AND d.id = ?"
            department_params = (user["department_id"],)
        elif user["role"] == "manager":
            departments_query += " AND d.id = ?"
            department_params = (user["department_id"],)
        departments_query += " GROUP BY d.id, d.name"
        departments = [
            dict(row)
            for row in connection.execute(departments_query, department_params)
        ]
        department_order = {name: index for index, name in enumerate(DEPARTMENT_NAMES)}
        departments.sort(
            key=lambda department: (
                department_order.get(department["name"], len(department_order)),
                department["name"].casefold(),
            )
        )
        if user["role"] == "admin":
            positions = [
                dict(row)
                for row in connection.execute(
                    "SELECT id, name FROM positions WHERE active = 1 ORDER BY name COLLATE NOCASE"
                )
            ]
        else:
            position_clause = "e.department_id = ?" if user["role"] == "manager" else "e.id = ?"
            position_scope = (
                user["department_id"] if user["role"] == "manager" else user["employee_id"]
            )
            positions = [
                dict(row)
                for row in connection.execute(
                    """SELECT DISTINCT p.id, p.name FROM positions p
                       JOIN employees e ON e.position_id = p.id
                       WHERE p.active = 1 AND e.active = 1
                         AND """ + position_clause + " ORDER BY p.name COLLATE NOCASE",
                    (position_scope,),
                )
            ]

        start = request.args.get("from", "")
        end = request.args.get("to", "")
        selected_department = request.args.get("department", "")
        selected_employee = request.args.get("employee", "")
        clauses = []
        params: list[Any] = []
        if start:
            if not validate_date(start):
                return json_error("Некорректная дата начала периода.")
            clauses.append("w.date >= ?")
            params.append(start)
        if end:
            if not validate_date(end):
                return json_error("Некорректная дата окончания периода.")
            clauses.append("w.date <= ?")
            params.append(end)
        if selected_department:
            if not selected_department.isdigit():
                return json_error("Некорректное подразделение.")
            clauses.append("w.department_id = ?")
            params.append(int(selected_department))
        if selected_employee:
            if not selected_employee.isdigit():
                return json_error("Некорректный сотрудник.")
            clauses.append("w.employee_id = ?")
            params.append(int(selected_employee))
        if user["role"] == "employee":
            clauses.append("w.employee_id = ?")
            params.append(user["employee_id"])
        elif user["role"] == "manager":
            clauses.append("w.department_id = ?")
            params.append(user["department_id"])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        works = [
            dict(row)
            for row in connection.execute(
                """SELECT w.id, w.employee_id, w.created_by, w.date, w.start_time, w.end_time,
                          w.title, w.employee_name, w.department_id,
                          w.department_name, w.position_name
                   FROM works w""" + where + " ORDER BY w.date DESC, w.start_time DESC LIMIT 5000",
                params,
            )
        ]
        directory = employee_rows(connection, user)
        if user["role"] in {"admin", "manager"}:
            directory_sql = """SELECT e.id, e.name, e.department_id, d.name AS department,
                                      e.position_id, p.name AS position, e.active
                               FROM employees e
                               JOIN departments d ON d.id = e.department_id
                               JOIN positions p ON p.id = e.position_id"""
            directory_params: tuple[Any, ...] = ()
            if user["role"] == "manager":
                directory_sql += " WHERE e.department_id = ?"
                directory_params = (user["department_id"],)
            directory_sql += " ORDER BY e.active DESC, e.name COLLATE NOCASE"
            directory = [
                dict(row)
                for row in connection.execute(directory_sql, directory_params)
            ]
        return jsonify(
            departments=departments,
            positions=positions,
            employees=employee_rows(connection, user),
            directory=directory,
            works=works,
        )

    @app.post("/api/works")
    @login_required
    def create_work():
        payload = parse_json_object()
        if payload is None:
            return json_error("Проверьте поля записи.")
        connection = get_database()
        try:
            employee_id = int(payload.get("employee_id"))
        except (TypeError, ValueError):
            return json_error("Выберите сотрудника.")
        employee = connection.execute(
            """SELECT e.id, e.name, e.department_id, d.name AS department,
                      e.position_id, p.name AS position, e.active
               FROM employees e JOIN departments d ON d.id = e.department_id
               JOIN positions p ON p.id = e.position_id WHERE e.id = ?""",
            (employee_id,),
        ).fetchone()
        if not employee or not employee["active"]:
            return json_error("Сотрудник не найден или неактивен.")
        if g.user["role"] == "employee" and employee_id != g.user["employee_id"]:
            return json_error("Сотрудник может вносить записи только за себя.", 403)
        if g.user["role"] == "manager" and employee["department_id"] != g.user["department_id"]:
            return json_error("Нельзя добавлять записи в другое подразделение.", 403)
        work_date = payload.get("date")
        start_time = payload.get("start_time")
        end_time = payload.get("end_time")
        title = payload.get("title")
        if not validate_date(work_date):
            return json_error("Укажите корректную дату выполнения.")
        if not isinstance(start_time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start_time):
            return json_error("Укажите корректное время начала.")
        if not isinstance(end_time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", end_time):
            return json_error("Укажите корректное время завершения.")
        if start_time >= end_time:
            return json_error("Время завершения должно быть позже времени начала.")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
            return json_error("Название работы обязательно (до 200 символов).")
        connection.execute("BEGIN IMMEDIATE")
        overlap = connection.execute(
            """SELECT 1 FROM works WHERE employee_id = ? AND date = ?
               AND start_time < ? AND end_time > ? LIMIT 1""",
            (employee_id, work_date, end_time, start_time),
        ).fetchone()
        if overlap:
            connection.rollback()
            return json_error("Время пересекается с другой записью этого сотрудника.")
        connection.execute(
            """INSERT INTO works
               (employee_id, created_by, department_id, date, start_time, end_time,
                title, employee_name, department_name, position_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                employee_id,
                g.user["id"],
                employee["department_id"],
                work_date,
                start_time,
                end_time,
                title.strip(),
                employee["name"],
                employee["department"],
                employee["position"],
                utc_now(),
            ),
        )
        connection.commit()
        return jsonify(ok=True), 201

    @app.delete("/api/works/<int:work_id>")
    @login_required
    def delete_work(work_id: int):
        connection = get_database()
        work = connection.execute(
            "SELECT employee_id, department_id, created_by FROM works WHERE id = ?",
            (work_id,),
        ).fetchone()
        if work is None:
            return json_error("Запись не найдена.", 404)
        if g.user["role"] == "employee" and work["created_by"] != g.user["id"]:
            return json_error("Можно удалять только записи, добавленные вами.", 403)
        if g.user["role"] == "manager" and work["department_id"] != g.user["department_id"]:
            return json_error("Нельзя удалять записи другого подразделения.", 403)
        connection.execute("DELETE FROM works WHERE id = ?", (work_id,))
        connection.commit()
        return jsonify(ok=True)

    @app.post("/api/departments")
    @roles_required("admin")
    def create_department():
        payload = parse_json_object()
        value = payload.get("name") if payload else None
        name = value.strip() if isinstance(value, str) else ""
        if not name or len(name) > 120:
            return json_error("Укажите название подразделения (до 120 символов).")
        try:
            connection = get_database()
            connection.execute("INSERT INTO departments(name) VALUES (?)", (name,))
            connection.commit()
        except sqlite3.IntegrityError:
            return json_error("Такое подразделение уже существует.")
        return jsonify(ok=True), 201

    @app.delete("/api/departments/<int:department_id>")
    @roles_required("admin")
    def delete_department(department_id: int):
        connection = get_database()
        department = connection.execute(
            "SELECT id FROM departments WHERE id = ?", (department_id,)
        ).fetchone()
        if department is None:
            return json_error("Подразделение не найдено.", 404)
        employee_count = connection.execute(
            "SELECT COUNT(*) FROM employees WHERE department_id = ?", (department_id,)
        ).fetchone()[0]
        if employee_count:
            return json_error(
                "Сначала удалите сотрудников этого подразделения. История работ будет сохранена.",
                409,
            )
        connection.execute(
            "UPDATE works SET department_id = NULL WHERE department_id = ?", (department_id,)
        )
        connection.execute("DELETE FROM departments WHERE id = ?", (department_id,))
        connection.commit()
        return jsonify(ok=True)

    @app.post("/api/positions")
    @roles_required("admin")
    def create_position():
        payload = parse_json_object()
        value = payload.get("name") if payload else None
        name = value.strip() if isinstance(value, str) else ""
        if not name or len(name) > POSITION_NAME_MAX_LENGTH:
            return json_error(
                f"Укажите название должности (до {POSITION_NAME_MAX_LENGTH} символов)."
            )
        try:
            connection = get_database()
            connection.execute("INSERT INTO positions(name) VALUES (?)", (name,))
            connection.commit()
        except sqlite3.IntegrityError:
            return json_error("Такая должность уже существует.")
        return jsonify(ok=True), 201

    @app.delete("/api/positions/<int:position_id>")
    @roles_required("admin")
    def delete_position(position_id: int):
        connection = get_database()
        position = connection.execute(
            "SELECT id FROM positions WHERE id = ?", (position_id,)
        ).fetchone()
        if position is None:
            return json_error("Должность не найдена.", 404)
        employee_count = connection.execute(
            "SELECT COUNT(*) FROM employees WHERE position_id = ?", (position_id,)
        ).fetchone()[0]
        if employee_count:
            return json_error(
                "Нельзя удалить должность, назначенную сотрудникам. Сначала удалите их карточки.",
                409,
            )
        connection.execute("DELETE FROM positions WHERE id = ?", (position_id,))
        connection.commit()
        return jsonify(ok=True)

    @app.post("/api/employees")
    @roles_required("admin", "manager")
    def create_employee():
        payload = parse_json_object()
        if payload is None:
            return json_error("Проверьте поля сотрудника.")
        name = payload.get("name", "").strip() if isinstance(payload.get("name"), str) else ""
        person_fields = ("surname", "first_name", "patronymic")
        if any(field in payload for field in person_fields):
            person_name = []
            for field in person_fields:
                value = payload.get(field)
                part = value.strip() if isinstance(value, str) else ""
                if not part or len(part) > 80:
                    return json_error("Заполните фамилию, имя и отчество (до 80 символов каждое).")
                person_name.append(part)
            name = " ".join(person_name)
        try:
            department_id = int(payload.get("department_id"))
            position_id = int(payload.get("position_id"))
        except (TypeError, ValueError):
            return json_error("Выберите подразделение и должность.")
        if not name or len(name) > 242:
            return json_error("Укажите Ф.И.О. сотрудника (до 80 символов на каждую часть).")
        if g.user["role"] == "manager" and department_id != g.user["department_id"]:
            return json_error("Руководитель может добавлять сотрудников только своего отдела.", 403)
        connection = get_database()
        department = connection.execute(
            "SELECT 1 FROM departments WHERE id = ? AND active = 1", (department_id,)
        ).fetchone()
        position = connection.execute(
            "SELECT 1 FROM positions WHERE id = ? AND active = 1", (position_id,)
        ).fetchone()
        if not department or not position:
            return json_error("Выбранное подразделение или должность недоступны.")
        try:
            connection.execute(
                """INSERT INTO employees(name, department_id, position_id)
                   VALUES (?, ?, ?)""",
                (name, department_id, position_id),
            )
            connection.commit()
        except sqlite3.IntegrityError:
            return json_error("Такой сотрудник уже есть в этом подразделении.")
        return jsonify(ok=True), 201

    @app.post("/api/employees/<int:employee_id>/status")
    @roles_required("admin", "manager")
    def employee_status(employee_id: int):
        payload = parse_json_object()
        active = payload.get("active") if payload else None
        if not isinstance(active, bool):
            return json_error("Некорректный статус.")
        connection = get_database()
        employee = connection.execute(
            "SELECT department_id FROM employees WHERE id = ?", (employee_id,)
        ).fetchone()
        if employee is None:
            return json_error("Сотрудник не найден.", 404)
        if g.user["role"] == "manager" and employee["department_id"] != g.user["department_id"]:
            return json_error("Нельзя изменять сотрудника другого подразделения.", 403)
        if not active and employee_id == g.user["employee_id"]:
            return json_error("Нельзя отключить собственную карточку сотрудника.")
        connection.execute("UPDATE employees SET active = ? WHERE id = ?", (int(active), employee_id))
        if not active:
            connection.execute(
                """UPDATE users SET active = 0, auth_version = auth_version + 1
                   WHERE employee_id = ?""",
                (employee_id,),
            )
        connection.commit()
        return jsonify(ok=True)

    @app.delete("/api/employees/<int:employee_id>")
    @roles_required("admin")
    def delete_employee(employee_id: int):
        connection = get_database()
        employee = connection.execute(
            "SELECT department_id FROM employees WHERE id = ?", (employee_id,)
        ).fetchone()
        if employee is None:
            return json_error("Сотрудник не найден.", 404)
        if employee_id == g.user["employee_id"]:
            return json_error("Нельзя удалить собственную карточку сотрудника.")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """UPDATE users SET employee_id = NULL, active = 0,
                      auth_version = auth_version + 1
               WHERE employee_id = ?""",
            (employee_id,),
        )
        connection.execute(
            "UPDATE works SET employee_id = NULL WHERE employee_id = ?", (employee_id,)
        )
        connection.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
        connection.commit()
        return jsonify(ok=True)

    @app.get("/api/users")
    @roles_required("admin")
    def get_users():
        rows = get_database().execute(
            """SELECT u.id, u.username, u.role, u.active, e.id AS employee_id,
                      e.name AS employee_name, d.name AS department
               FROM users u LEFT JOIN employees e ON e.id = u.employee_id
               LEFT JOIN departments d ON d.id = e.department_id
               ORDER BY u.username COLLATE NOCASE"""
        )
        return jsonify(users=[dict(row) for row in rows])

    @app.post("/api/users")
    @roles_required("admin")
    def create_user():
        payload = parse_json_object()
        if payload is None:
            return json_error("Проверьте данные учётной записи.")
        username = payload.get("username", "").strip() if isinstance(payload.get("username"), str) else ""
        password = payload.get("password", "")
        role = payload.get("role")
        if not USERNAME_PATTERN.fullmatch(username):
            return json_error("Логин: 3–80 символов — латиница, цифры, точка, дефис или подчёркивание.")
        if not valid_user_password(password):
            return json_error(
                "Пароль должен содержать минимум 10 символов, 2 заглавные и 2 строчные латинские буквы."
            )
        if not isinstance(role, str) or role not in {"employee", "manager", "admin"}:
            return json_error("Выберите допустимую роль учётной записи.")
        connection = get_database()
        employee_id = None
        if role != "admin":
            try:
                employee_id = int(payload.get("employee_id"))
            except (TypeError, ValueError):
                return json_error("Выберите сотрудника для учётной записи.")
            employee = connection.execute(
                "SELECT active FROM employees WHERE id = ?", (employee_id,)
            ).fetchone()
            if employee is None or not employee["active"]:
                return json_error("Сотрудник не найден или неактивен.")
            if connection.execute(
                "SELECT 1 FROM users WHERE employee_id = ?", (employee_id,)
            ).fetchone():
                return json_error("Для этого сотрудника уже создана учётная запись.")
        salt, password_hash = hash_password(password)
        try:
            connection.execute(
                """INSERT INTO users
                   (username, password_salt, password_hash, role, employee_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (username, salt, password_hash, role, employee_id, utc_now()),
            )
            connection.commit()
        except sqlite3.IntegrityError:
            return json_error("Такой логин уже занят.")
        return jsonify(ok=True), 201

    @app.post("/api/users/<int:user_id>/password")
    @roles_required("admin")
    def reset_user_password(user_id: int):
        payload = parse_json_object()
        password = payload.get("password") if payload else None
        if not valid_user_password(password):
            return json_error(
                "Пароль должен содержать минимум 10 символов, 2 заглавные и 2 строчные латинские буквы."
            )
        connection = get_database()
        target = connection.execute(
            "SELECT 1 FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if target is None:
            return json_error("Учётная запись не найдена.", 404)
        salt, password_hash = hash_password(password)
        connection.execute(
            """UPDATE users SET password_salt = ?, password_hash = ?,
                      auth_version = auth_version + 1
               WHERE id = ?""",
            (salt, password_hash, user_id),
        )
        connection.commit()
        return jsonify(ok=True)

    @app.post("/api/users/<int:user_id>/status")
    @roles_required("admin")
    def user_status(user_id: int):
        payload = parse_json_object()
        active = payload.get("active") if payload else None
        if not isinstance(active, bool):
            return json_error("Некорректный статус.")
        connection = get_database()
        target = connection.execute(
            "SELECT role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if target is None:
            return json_error("Учётная запись не найдена.", 404)
        if user_id == g.user["id"] and not active:
            return json_error("Нельзя отключить собственную учётную запись.")
        if target["role"] == "admin" and not active:
            remaining = connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id != ?",
                (user_id,),
            ).fetchone()[0]
            if remaining == 0:
                return json_error("Нельзя отключить последнюю активную учётную запись администратора.")
        connection.execute(
            "UPDATE users SET active = ?, auth_version = auth_version + 1 WHERE id = ?",
            (int(active), user_id),
        )
        connection.commit()
        return jsonify(ok=True)

    return app


app = create_app()


if __name__ == "__main__":
    from waitress import serve

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    serve(app, host=host, port=port, threads=8)
