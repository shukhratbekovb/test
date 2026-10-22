import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


TEMP_ROOT = tempfile.TemporaryDirectory()
TEST_DATABASES = Path(TEMP_ROOT.name)
os.environ["DATABASE_PATH"] = str(TEST_DATABASES / "workload.sqlite3")
os.environ["SECRET_KEY"] = "test-only-secret-key-that-is-at-least-32-bytes"
os.environ["ADMIN_USERNAME"] = "test-admin"
os.environ["ADMIN_PASSWORD"] = "test-admin-password-strong"

import app as workload_app  # noqa: E402


class WorkloadAppTests(unittest.TestCase):
    def test_health_endpoint(self):
        response = self.app.test_client().get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        self.assertNotIn("Set-Cookie", response.headers)

    def setUp(self):
        os.environ["DATABASE_PATH"] = str(TEST_DATABASES / f"{uuid4().hex}.sqlite3")
        self.app = workload_app.create_app()
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.admin = self.login("test-admin", "test-admin-password-strong")
        data = self.get_json(self.admin, "/api/data")
        self.department_a = data["departments"][0]
        self.department_b = data["departments"][1]
        position = data["positions"][0]
        self.employee_a = self.create_employee(
            self.admin, "Александров Алексей Александрович", self.department_a["id"], position["id"]
        )
        self.employee_b = self.create_employee(
            self.admin, "Борисова Мария Павловна", self.department_b["id"], position["id"]
        )
        self.create_account(
            self.admin, self.employee_a["id"], "manager-a", "Manager-Aa-password-Strong", "manager"
        )
        employee_a_account = self.create_employee(
            self.admin, "Абрамов Антон Сергеевич", self.department_a["id"], position["id"]
        )
        self.employee_a_account = employee_a_account
        self.create_account(
            self.admin, employee_a_account["id"], "employee-a", "Employee-Aa-password-Strong"
        )
        self.create_account(
            self.admin, self.employee_b["id"], "employee-b", "Employee-Bb-password-Strong"
        )

    def tearDown(self):
        self.admin = None

    def login(self, username, password):
        client = self.app.test_client()
        csrf = client.get("/api/me").get_json()["csrf_token"]
        response = client.post(
            "/api/login",
            json={"username": username, "password": password},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return client

    def post_json(self, client, url, payload):
        csrf = client.get("/api/me").get_json()["csrf_token"]
        return client.post(
            url, json=payload, headers={"X-CSRF-Token": csrf}
        )

    def get_json(self, client, url):
        response = client.get(url)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def create_employee(self, client, name, department_id, position_id):
        response = self.post_json(
            client,
            "/api/employees",
            {"name": name, "department_id": department_id, "position_id": position_id},
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        employees = self.get_json(client, "/api/data")["directory"]
        return next(employee for employee in employees if employee["name"] == name)

    def create_account(self, client, employee_id, username, password, role="employee"):
        response = self.post_json(
            client,
            "/api/users",
            {
                "employee_id": employee_id,
                "username": username,
                "password": password,
                "role": role,
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())

    def test_department_names_migrate_and_keep_requested_order(self):
        expected_names = list(workload_app.DEPARTMENT_NAMES)
        data = self.get_json(self.admin, "/api/data")
        self.assertEqual([item["name"] for item in data["departments"]], expected_names)
        self.assertTrue(all("employee_count" in item for item in data["departments"]))

        response = self.post_json(
            self.admin,
            "/api/works",
            {
                "employee_id": self.employee_a["id"],
                "date": "2026-10-22",
                "start_time": "09:00",
                "end_time": "10:00",
                "title": "Проверка миграции названий",
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        database = workload_app.connect_database()
        try:
            work_id = database.execute("SELECT MAX(id) FROM works").fetchone()[0]
            for old_name, new_name in workload_app.LEGACY_DEPARTMENT_NAMES:
                database.execute(
                    "UPDATE departments SET name = ? WHERE name = ?", (old_name, new_name)
                )
                database.execute(
                    "UPDATE works SET department_name = ? WHERE department_name = ?",
                    (old_name, new_name),
                )
            database.commit()
        finally:
            database.close()

        workload_app.initialize_database()
        migrated = self.get_json(self.admin, "/api/data")
        self.assertEqual([item["name"] for item in migrated["departments"]], expected_names)
        self.assertEqual(
            [work["department_name"] for work in migrated["works"] if work["id"] == work_id],
            [expected_names[0]],
        )

    def test_employee_form_fields_and_long_position_name(self):
        long_position = "Старший главный инженер по комплексному проектированию " * 4
        position_response = self.post_json(
            self.admin, "/api/positions", {"name": long_position}
        )
        self.assertEqual(position_response.status_code, 201, position_response.get_json())
        position = next(
            item for item in self.get_json(self.admin, "/api/data")["positions"]
            if item["name"] == long_position.strip()
        )
        response = self.post_json(
            self.admin,
            "/api/employees",
            {
                "department_id": self.department_a["id"],
                "position_id": position["id"],
                "surname": "Тестов",
                "first_name": "Тест",
                "patronymic": "Тестович",
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        data = self.get_json(self.admin, "/api/data")
        employee = next(item for item in data["directory"] if item["name"] == "Тестов Тест Тестович")
        self.assertEqual(employee["position"], long_position.strip())
        department = next(
            item for item in data["departments"] if item["id"] == self.department_a["id"]
        )
        self.assertEqual(department["employee_count"], 3)

    def test_employee_account_password_requires_two_latin_cases_and_ten_characters(self):
        employee = self.create_employee(
            self.admin, "Новый Сотрудник Отчество", self.department_a["id"], self.get_json(
                self.admin, "/api/data"
            )["positions"][0]["id"]
        )
        invalid = self.post_json(
            self.admin,
            "/api/users",
            {
                "employee_id": employee["id"],
                "username": "new-employee",
                "password": "Aaaaa12345",
                "role": "employee",
            },
        )
        self.assertEqual(invalid.status_code, 400)

        valid = self.post_json(
            self.admin,
            "/api/users",
            {
                "employee_id": employee["id"],
                "username": "new-employee",
                "password": "ABcd1234-_",
                "role": "employee",
            },
        )
        self.assertEqual(valid.status_code, 201, valid.get_json())

    def test_employee_deletion_preserves_work_history_and_disables_account(self):
        employee = self.employee_a_account
        work = self.post_json(
            self.admin,
            "/api/works",
            {
                "employee_id": employee["id"],
                "date": "2026-10-22",
                "start_time": "10:00",
                "end_time": "11:00",
                "title": "Работа до удаления карточки",
            },
        )
        self.assertEqual(work.status_code, 201, work.get_json())

        csrf = self.admin.get("/api/me").get_json()["csrf_token"]
        response = self.admin.delete(
            f"/api/employees/{employee['id']}",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        database = workload_app.connect_database()
        try:
            saved_work = database.execute(
                "SELECT employee_id, employee_name, department_name FROM works ORDER BY id DESC LIMIT 1"
            ).fetchone()
            saved_user = database.execute(
                "SELECT employee_id, active FROM users WHERE username = 'employee-a'"
            ).fetchone()
        finally:
            database.close()
        self.assertIsNone(saved_work["employee_id"])
        self.assertEqual(saved_work["employee_name"], employee["name"])
        self.assertEqual(saved_work["department_name"], self.department_a["name"])
        self.assertIsNone(saved_user["employee_id"])
        self.assertEqual(saved_user["active"], 0)

    def test_department_and_position_deletion_respect_employee_references(self):
        populated_department = self.admin.delete(
            f"/api/departments/{self.department_a['id']}",
            headers={"X-CSRF-Token": self.admin.get("/api/me").get_json()["csrf_token"]},
        )
        self.assertEqual(populated_department.status_code, 409)
        populated_position_id = self.employee_a["position_id"]
        populated_position = self.admin.delete(
            f"/api/positions/{populated_position_id}",
            headers={"X-CSRF-Token": self.admin.get("/api/me").get_json()["csrf_token"]},
        )
        self.assertEqual(populated_position.status_code, 409)

        department = self.post_json(self.admin, "/api/departments", {"name": "Временное подразделение"})
        self.assertEqual(department.status_code, 201)
        department_id = next(
            item["id"] for item in self.get_json(self.admin, "/api/data")["departments"]
            if item["name"] == "Временное подразделение"
        )
        position = self.post_json(self.admin, "/api/positions", {"name": "Временная должность"})
        self.assertEqual(position.status_code, 201)
        position_id = next(
            item["id"] for item in self.get_json(self.admin, "/api/data")["positions"]
            if item["name"] == "Временная должность"
        )
        csrf = self.admin.get("/api/me").get_json()["csrf_token"]
        self.assertEqual(self.admin.delete(
            f"/api/departments/{department_id}", headers={"X-CSRF-Token": csrf}
        ).status_code, 200)
        self.assertEqual(self.admin.delete(
            f"/api/positions/{position_id}", headers={"X-CSRF-Token": csrf}
        ).status_code, 200)

    def test_data_and_mutations_are_limited_to_assigned_department(self):
        manager = self.login("manager-a", "Manager-Aa-password-Strong")
        data = self.get_json(manager, "/api/data")
        self.assertEqual([item["id"] for item in data["departments"]], [self.department_a["id"]])
        self.assertTrue(all(item["department_id"] == self.department_a["id"] for item in data["directory"]))
        manager_csrf = manager.get("/api/me").get_json()["csrf_token"]
        delete_employee = manager.delete(
            f"/api/employees/{self.employee_a_account['id']}",
            headers={"X-CSRF-Token": manager_csrf},
        )
        self.assertEqual(delete_employee.status_code, 403)

        response = self.post_json(
            manager,
            "/api/employees",
            {
                "name": "Несанкционированный сотрудник",
                "department_id": self.department_b["id"],
                "position_id": data["positions"][0]["id"],
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_employee_can_only_view_and_record_their_own_work(self):
        user_a = self.login("employee-a", "Employee-Aa-password-Strong")
        user_b = self.login("employee-b", "Employee-Bb-password-Strong")
        data_b = self.get_json(user_b, "/api/data")
        self.assertEqual([item["id"] for item in data_b["employees"]], [self.employee_b["id"]])

        foreign_work = {
            "employee_id": self.employee_a_account["id"],
            "date": "2026-10-21",
            "start_time": "09:00",
            "end_time": "10:00",
            "title": "Чужая запись",
        }
        response = self.post_json(user_b, "/api/works", foreign_work)
        self.assertEqual(response.status_code, 403)

        own_work = {**foreign_work, "employee_id": self.employee_a_account["id"], "title": "Работа"}
        created = self.post_json(user_a, "/api/works", own_work)
        self.assertEqual(created.status_code, 201, created.get_json())
        self.assertEqual(len(self.get_json(user_b, "/api/data")["works"]), 0)
        self.assertEqual(len(self.get_json(user_a, "/api/data")["works"]), 1)

    def test_overlapping_work_is_rejected_and_csrf_is_required(self):
        employee = self.login("employee-b", "Employee-Bb-password-Strong")
        work = {
            "employee_id": self.employee_b["id"],
            "date": "2026-10-21",
            "start_time": "09:00",
            "end_time": "10:00",
            "title": "Первый интервал",
        }
        self.assertEqual(self.post_json(employee, "/api/works", work).status_code, 201)
        overlapping = {**work, "start_time": "09:30", "end_time": "10:30"}
        self.assertEqual(self.post_json(employee, "/api/works", overlapping).status_code, 400)
        missing_csrf = employee.post("/api/works", json=work)
        self.assertEqual(missing_csrf.status_code, 400)

    def test_inactive_user_session_is_invalidated(self):
        user = self.login("employee-b", "Employee-Bb-password-Strong")
        response = self.post_json(
            self.admin,
            f"/api/employees/{self.employee_b['id']}/status",
            {"active": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(user.get("/api/data").status_code, 401)
        data = self.get_json(self.admin, "/api/data")
        employee = next(item for item in data["directory"] if item["id"] == self.employee_b["id"])
        department = next(
            item for item in data["departments"] if item["id"] == self.department_b["id"]
        )
        self.assertFalse(employee["active"])
        self.assertEqual(department["employee_count"], 0)

    def test_password_change_invalidates_other_sessions(self):
        first_session = self.login("employee-b", "Employee-Bb-password-Strong")
        second_session = self.login("employee-b", "Employee-Bb-password-Strong")
        response = self.post_json(
            first_session,
            "/api/password",
            {
                "old_password": "Employee-Bb-password-Strong",
                "new_password": "Employee-B-New-Password-Strong",
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["csrf_token"])
        self.assertTrue(self.get_json(first_session, "/api/me")["authenticated"])
        self.assertFalse(second_session.get("/api/me").get_json()["authenticated"])

        login_page = self.app.test_client()
        csrf = login_page.get("/api/me").get_json()["csrf_token"]
        old_password = login_page.post(
            "/api/login",
            json={"username": "employee-b", "password": "Employee-Bb-password-Strong"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(old_password.status_code, 401)
        new_password = login_page.post(
            "/api/login",
            json={"username": "employee-b", "password": "Employee-B-New-Password-Strong"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(new_password.status_code, 200)

    def test_admin_password_reset_revokes_existing_sessions(self):
        employee_session = self.login("employee-b", "Employee-Bb-password-Strong")
        users = self.get_json(self.admin, "/api/users")["users"]
        employee_user = next(item for item in users if item["username"] == "employee-b")
        response = self.post_json(
            self.admin,
            f"/api/users/{employee_user['id']}/password",
            {"password": "Admin-Reset-Password-Strong"},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertFalse(employee_session.get("/api/me").get_json()["authenticated"])

        login_page = self.app.test_client()
        csrf = login_page.get("/api/me").get_json()["csrf_token"]
        response = login_page.post(
            "/api/login",
            json={"username": "employee-b", "password": "Admin-Reset-Password-Strong"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200)

    def test_admin_can_create_another_admin_without_employee(self):
        response = self.post_json(
            self.admin,
            "/api/users",
            {
                "username": "second-admin",
                "password": "Second-Admin-Password-Strong",
                "role": "admin",
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        users = self.get_json(self.admin, "/api/users")["users"]
        new_admin = next(user for user in users if user["username"] == "second-admin")
        self.assertEqual(new_admin["role"], "admin")
        self.assertIsNone(new_admin["employee_id"])


if __name__ == "__main__":
    unittest.main()
