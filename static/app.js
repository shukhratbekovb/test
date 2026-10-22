"use strict";

const $ = (id) => document.getElementById(id);
const roleNames = { employee: "Сотрудник", manager: "Руководитель отдела", admin: "Администратор" };
let currentUser = null;
let csrfToken = "";
let selectedDepartmentId = null;
let applicationData = { departments: [], positions: [], employees: [], directory: [], works: [] };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET") headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(url, { credentials: "same-origin", ...options, headers });
  const result = response.status === 204 ? {} : await response.json();
  if (!response.ok) {
    if (response.status === 401 && currentUser) showLogin();
    throw new Error(result.error || "Не удалось выполнить запрос.");
  }
  return result;
}

function notify(message, isError = false) {
  const notice = $("notice");
  notice.textContent = message;
  notice.classList.toggle("error", isError);
  notice.classList.remove("hidden");
  window.clearTimeout(notice.timer);
  notice.timer = window.setTimeout(() => notice.classList.add("hidden"), 5000);
}

function displayError(error) {
  notify(error.message || "Произошла ошибка.", true);
}

function selectOptions(element, items, placeholder, valueKey = "id", labelKey = "name") {
  const previous = element.value;
  element.replaceChildren(new Option(placeholder, ""));
  for (const item of items) element.add(new Option(item[labelKey], String(item[valueKey])));
  if (items.some((item) => String(item[valueKey]) === previous)) element.value = previous;
}

function optionsForFilter() {
  const selected = $("filter-department").value;
  const employees = applicationData.employees.filter(
    (employee) => !selected || String(employee.department_id) === selected,
  );
  const filter = $("filter-employee");
  const previous = filter.value;
  filter.replaceChildren(new Option("Все сотрудники", ""));
  for (const employee of employees) {
    filter.add(new Option(`${employee.name} — ${employee.department}`, String(employee.id)));
  }
  if ([...filter.options].some((option) => option.value === previous)) filter.value = previous;
}

function updateFormOptions() {
  selectOptions($("filter-department"), applicationData.departments, "Все подразделения");
  selectOptions($("work-department"), applicationData.departments, "Выберите подразделение");
  selectOptions($("new-employee-department"), applicationData.departments, "Выберите подразделение");
  selectOptions($("new-employee-position"), applicationData.positions, "Выберите должность");
  optionsForFilter();

  const workDepartment = $("work-department");
  if (currentUser.role === "employee") {
    workDepartment.value = String(currentUser.department_id || "");
    workDepartment.disabled = true;
    $("work-employee").replaceChildren(
      new Option(currentUser.employee_name || "Сотрудник не назначен", String(currentUser.employee_id || "")),
    );
    $("work-employee").disabled = true;
    $("work-position").value = applicationData.employees.find(
      (employee) => employee.id === currentUser.employee_id,
    )?.position || "";
  } else {
    workDepartment.disabled = false;
    updateWorkEmployees(false);
  }

  if (currentUser.role === "manager") {
    $("new-employee-department").value = String(currentUser.department_id || "");
    $("new-employee-department").disabled = true;
  } else {
    $("new-employee-department").disabled = false;
  }
}

function updateAccountEmployeeOptions(users) {
  const employeesWithAccounts = new Set(
    users.filter((user) => user.employee_id !== null).map((user) => String(user.employee_id)),
  );
  const employeesWithoutAccounts = applicationData.directory.filter(
    (employee) => employee.active && !employeesWithAccounts.has(String(employee.id)),
  );
  selectOptions(
    $("account-employee"),
    employeesWithoutAccounts,
    employeesWithoutAccounts.length ? "Выберите сотрудника" : "Нет сотрудников без учётной записи",
    "id",
    "name",
  );
  $("account-employee").disabled = employeesWithoutAccounts.length === 0;
  $("account-form").querySelector("button[type=submit]").disabled = employeesWithoutAccounts.length === 0;
}

function updateWorkEmployees(reset) {
  const selectedDepartment = $("work-department").value;
  const employees = applicationData.employees.filter(
    (employee) => !selectedDepartment || String(employee.department_id) === selectedDepartment,
  );
  const select = $("work-employee");
  if (reset) select.value = "";
  selectOptions(select, employees, "Выберите сотрудника", "id", "name");
  updateWorkPosition();
}

function updateWorkPosition() {
  const employee = applicationData.employees.find(
    (item) => String(item.id) === $("work-employee").value,
  );
  $("work-position").value = employee?.position || "";
}

function renderDashboard() {
  const rows = applicationData.works;
  const total = rows.reduce((sum, work) => sum + minutes(work) / 60, 0);
  const people = new Set(rows.map((work) => work.employee_id)).size;
  const from = $("from").value ? new Date(`${$("from").value}T12:00:00`) : null;
  const to = $("to").value ? new Date(`${$("to").value}T12:00:00`) : null;
  let workDays = 0;
  if (from && to && from <= to) {
    for (const date = new Date(from); date <= to; date.setDate(date.getDate() + 1)) {
      if (date.getDay() !== 0 && date.getDay() !== 6) workDays += 1;
    }
  }
  const dailyNorm = Number($("daily-norm").value) || 8;
  const capacity = workDays * dailyNorm;
  $("stat-hours").textContent = `${round(total)} ч`;
  $("stat-records").textContent = String(rows.length);
  $("stat-employees").textContent = String(people);
  $("stat-utilization").textContent = `${people && capacity ? round(total / people / capacity * 100) : 0}%`;

  const grouped = new Map();
  for (const work of rows) {
    const key = `${work.employee_id}:${work.employee_name}:${work.department_name}`;
    const entry = grouped.get(key) || {
      id: work.employee_id,
      name: work.employee_name,
      department: work.department_name,
      total: 0,
    };
    entry.total += minutes(work) / 60;
    grouped.set(key, entry);
  }
  const summaries = [...grouped.values()].sort((a, b) => b.total - a.total);
  $("department-summary").innerHTML = applicationData.departments.length
    ? '<div class="table-scroll"><table><thead><tr><th>Подразделение</th>'
      + '<th>Количество сотрудников</th></tr></thead><tbody>'
      + applicationData.departments.map((department) => (
        `<tr class="${String(department.id) === String(selectedDepartmentId) ? "selected-department" : ""}">`
        + `<td><button class="department-select" type="button" data-department-id="${department.id}"`
        + ` aria-pressed="${String(department.id) === String(selectedDepartmentId)}">`
        + `${escapeHtml(department.name)}</button></td><td>${department.employee_count}</td></tr>`
      )).join("")
      + "</tbody></table></div>"
    : '<p class="empty-row">Подразделения не найдены.</p>';
  renderDepartmentEmployees();
  $("summary-rows").innerHTML = summaries.length ? summaries.map((entry) => {
    const utilization = capacity ? entry.total / capacity * 100 : 0;
    return `<tr><td>${escapeHtml(entry.name)}</td><td>${escapeHtml(entry.department)}</td>`
      + `<td><strong>${round(entry.total)} ч</strong></td><td><progress class="loadbar ${utilization > 100 ? "over" : ""}" max="100" value="${Math.min(100, Math.max(0, utilization))}"></progress>`
      + `<small>${round(utilization)}%</small></td></tr>`;
  }).join("") : '<tr><td colspan="4" class="empty-row">За выбранный период записей нет</td></tr>';

  $("work-rows").innerHTML = rows.length ? rows.map((work) => {
    const canDelete = currentUser.role !== "employee" || work.created_by === currentUser.id;
    return `<tr><td>${formatDate(work.date)}</td><td>${escapeHtml(work.department_name)}</td>`
      + `<td>${escapeHtml(work.employee_name)}</td><td>${escapeHtml(work.position_name)}</td>`
      + `<td>${escapeHtml(work.title)}</td><td>${escapeHtml(work.start_time)}</td>`
      + `<td>${escapeHtml(work.end_time)}</td><td>${round(minutes(work) / 60)}</td>`
      + `<td>${canDelete ? `<button class="button button-secondary" data-delete-work="${work.id}">Удалить</button>` : ""}</td></tr>`;
  }).join("") : '<tr><td colspan="9" class="empty-row">Нет записей за выбранный период</td></tr>';
}

function minutes(work) {
  const [startHours, startMinutes] = work.start_time.split(":").map(Number);
  const [endHours, endMinutes] = work.end_time.split(":").map(Number);
  return endHours * 60 + endMinutes - startHours * 60 - startMinutes;
}

function round(value) {
  return Math.round(value * 10) / 10;
}

function formatDate(value) {
  const [year, month, day] = value.split("-");
  return `${day}.${month}.${year}`;
}

function renderDirectory() {
  const employees = applicationData.directory;
  $("directory-rows").innerHTML = employees.length ? employees.map((employee) => (
    `<tr><td>${escapeHtml(employee.name)}</td><td>${escapeHtml(employee.department)}</td>`
    + `<td>${escapeHtml(employee.position)}</td><td>${employee.active ? "Активен" : "Неактивен"}</td>`
    + `<td><button class="button button-secondary" data-employee-status="${employee.id}" data-active="${!employee.active}">`
    + `${employee.active ? "Уволить" : "Восстановить"}</button> `
    + `${currentUser.role === "admin" ? `<button class="button button-danger" data-delete-employee="${employee.id}">Удалить</button>` : ""}</td></tr>`
  )).join("") : '<tr><td colspan="5" class="empty-row">Справочник пока пуст.</td></tr>';
}

function renderDepartmentEmployees() {
  const panel = $("department-employees");
  if (selectedDepartmentId === null) {
    panel.innerHTML = '<p class="empty-row">Выберите подразделение, чтобы посмотреть сотрудников.</p>';
    return;
  }
  const department = applicationData.departments.find(
    (item) => String(item.id) === String(selectedDepartmentId),
  );
  if (!department) {
    selectedDepartmentId = null;
    panel.innerHTML = '<p class="empty-row">Выберите подразделение, чтобы посмотреть сотрудников.</p>';
    return;
  }
  const employees = applicationData.directory.filter(
    (employee) => String(employee.department_id) === String(department.id),
  );
  panel.innerHTML = `<h3>${escapeHtml(department.name)}</h3>`
    + (employees.length
      ? `<ul class="department-employee-list">${employees.map((employee) => (
        `<li><span class="staff-status ${employee.active ? "in-staff" : "dismissed"}"`
        + ` role="img" aria-label="${employee.active ? "В штате" : "Уволен"}">`
        + `${employee.active ? "✓" : "✕"}</span><span>${escapeHtml(employee.name)}</span>`
        + `<span class="staff-status-label">${employee.active ? "В штате" : "Уволен"}</span></li>`
      )).join("")}</ul>`
      : '<p class="empty-row">В этом подразделении пока нет сотрудников.</p>');
}

function renderReferenceLists() {
  $("department-rows").innerHTML = applicationData.departments.length
    ? applicationData.departments.map((department) => (
      `<tr><td>${escapeHtml(department.name)}</td><td>`
      + `<button class="button button-danger" data-delete-department="${department.id}">Удалить</button>`
      + "</td></tr>"
    )).join("")
    : '<tr><td colspan="2" class="empty-row">Подразделений нет.</td></tr>';
  $("position-rows").innerHTML = applicationData.positions.length
    ? applicationData.positions.map((position) => (
      `<tr><td>${escapeHtml(position.name)}</td><td>`
      + `<button class="button button-danger" data-delete-position="${position.id}">Удалить</button>`
      + "</td></tr>"
    )).join("")
    : '<tr><td colspan="2" class="empty-row">Должностей нет.</td></tr>';
}

async function loadData() {
  const query = new URLSearchParams();
  if ($("from").value) query.set("from", $("from").value);
  if ($("to").value) query.set("to", $("to").value);
  if ($("filter-department").value) query.set("department", $("filter-department").value);
  if ($("filter-employee").value) query.set("employee", $("filter-employee").value);
  const result = await api(`/api/data?${query.toString()}`);
  applicationData = result;
  updateFormOptions();
  renderDashboard();
  renderDirectory();
  if (currentUser.role === "admin") {
    const { users } = await api("/api/users");
    updateAccountEmployeeOptions(users);
    renderReferenceLists();
  }
}

function showLogin() {
  currentUser = null;
  $("application").classList.add("hidden");
  $("account").classList.add("hidden");
  $("login-screen").classList.remove("hidden");
}

async function showApplication(user) {
  currentUser = user;
  $("login-screen").classList.add("hidden");
  $("application").classList.remove("hidden");
  $("account").classList.remove("hidden");
  $("account-name").textContent = user.employee_name || user.username;
  $("account-role-badge").textContent = user.role_name || roleNames[user.role];
  $("directory").classList.toggle("hidden", user.role === "employee");
  document.querySelectorAll(".admin-only").forEach((element) => {
    element.classList.toggle("hidden", user.role !== "admin");
  });
  const today = new Date();
  const startOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);
  $("from").value = toIsoDate(startOfMonth);
  $("to").value = toIsoDate(today);
  $("work-date").value = toIsoDate(today);
  await loadData();
}

function toIsoDate(value) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

async function updateSession() {
  const result = await api("/api/me");
  csrfToken = result.csrf_token;
  if (result.authenticated) await showApplication(result.user);
  else showLogin();
}

function reportError(promise) {
  promise.catch(displayError);
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    await api("/api/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("login-username").value,
        password: $("login-password").value,
      }),
    });
    $("login-password").value = "";
    await updateSession();
  } catch (error) {
    $("login-error").textContent = error.message;
  }
});

$("logout").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST", body: "{}" });
    await updateSession();
  } catch (error) {
    displayError(error);
  }
});

for (const id of ["from", "to", "filter-employee", "daily-norm"]) {
  $(id).addEventListener("change", () => reportError(loadData()));
}
$("filter-department").addEventListener("change", () => {
  $("filter-employee").value = "";
  optionsForFilter();
  reportError(loadData());
});
$("work-department").addEventListener("change", () => updateWorkEmployees(true));
$("work-employee").addEventListener("change", updateWorkPosition);

$("work-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/works", {
      method: "POST",
      body: JSON.stringify({
        employee_id: $("work-employee").value,
        date: $("work-date").value,
        title: $("work-title").value,
        start_time: $("work-start").value,
        end_time: $("work-end").value,
      }),
    });
    $("work-title").value = "";
    await loadData();
    notify("Запись о работе добавлена.");
  } catch (error) {
    displayError(error);
  }
});

$("work-rows").addEventListener("click", (event) => {
  const button = event.target.closest("[data-delete-work]");
  if (!button || !window.confirm("Удалить эту запись о работе?")) return;
  reportError(api(`/api/works/${button.dataset.deleteWork}`, { method: "DELETE" })
    .then(loadData).then(() => notify("Запись удалена.")));
});

$("department-summary").addEventListener("click", (event) => {
  const button = event.target.closest("[data-department-id]");
  if (!button) return;
  selectedDepartmentId = button.dataset.departmentId;
  renderDashboard();
});

$("department-rows").addEventListener("click", (event) => {
  const button = event.target.closest("[data-delete-department]");
  if (!button) return;
  if (!window.confirm("Удалить это подразделение? История работ сохранится.")) return;
  reportError(api(`/api/departments/${button.dataset.deleteDepartment}`, { method: "DELETE" })
    .then(loadData).then(() => notify("Подразделение удалено.")));
});

$("position-rows").addEventListener("click", (event) => {
  const button = event.target.closest("[data-delete-position]");
  if (!button) return;
  if (!window.confirm("Удалить эту должность?")) return;
  reportError(api(`/api/positions/${button.dataset.deletePosition}`, { method: "DELETE" })
    .then(loadData).then(() => notify("Должность удалена.")));
});

$("department-form").addEventListener("submit", (event) => {
  event.preventDefault();
  reportError(api("/api/departments", {
    method: "POST",
    body: JSON.stringify({ name: $("new-department").value }),
  }).then(() => {
    $("new-department").value = "";
    return loadData();
  }).then(() => notify("Подразделение добавлено.")));
});

$("position-form").addEventListener("submit", (event) => {
  event.preventDefault();
  reportError(api("/api/positions", {
    method: "POST",
    body: JSON.stringify({ name: $("new-position").value }),
  }).then(() => {
    $("new-position").value = "";
    return loadData();
  }).then(() => notify("Должность добавлена.")));
});

$("employee-form").addEventListener("submit", (event) => {
  event.preventDefault();
  reportError(api("/api/employees", {
    method: "POST",
    body: JSON.stringify({
      department_id: $("new-employee-department").value,
      position_id: $("new-employee-position").value,
      surname: $("new-employee-surname").value,
      first_name: $("new-employee-first-name").value,
      patronymic: $("new-employee-patronymic").value,
    }),
  }).then(() => {
    $("employee-form").reset();
    return loadData();
  }).then(() => notify("Сотрудник добавлен.")));
});

$("directory-rows").addEventListener("click", (event) => {
  const button = event.target.closest("[data-employee-status]");
  if (button) {
    const active = button.dataset.active === "true";
    reportError(api(`/api/employees/${button.dataset.employeeStatus}/status`, {
      method: "POST",
      body: JSON.stringify({ active }),
    }).then(loadData).then(() => notify(active ? "Сотрудник восстановлен." : "Сотрудник уволен.")));
    return;
  }
  const deleteButton = event.target.closest("[data-delete-employee]");
  if (!deleteButton) return;
  if (!window.confirm("Удалить карточку сотрудника? Его учётная запись будет отключена, история работ сохранится.")) return;
  reportError(api(`/api/employees/${deleteButton.dataset.deleteEmployee}`, { method: "DELETE" })
    .then(loadData).then(() => notify("Сотрудник удалён.")));
});

$("account-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const password = $("account-password").value;
  if (password.length < 10 || (password.match(/[A-Z]/g) || []).length < 2
      || (password.match(/[a-z]/g) || []).length < 2) {
    notify("Пароль: минимум 10 символов, 2 заглавные и 2 строчные латинские буквы.", true);
    return;
  }
  reportError(api("/api/users", {
    method: "POST",
    body: JSON.stringify({
      employee_id: $("account-employee").value,
      role: "employee",
      username: $("account-username").value,
      password,
    }),
  }).then(() => {
    $("account-form").reset();
    return loadData();
  }).then(() => notify("Учётная запись создана. Передайте сотруднику логин и временный пароль безопасным способом.")));
});

$("export").addEventListener("click", () => {
  const columns = ["Дата", "Подразделение", "Ф.И.О.", "Должность", "Работа", "Начало", "Завершение", "Часы"];
  const escapeCsv = (value) => `"${String(value).replace(/"/g, '""')}"`;
  const records = applicationData.works.map((work) => [
    work.date, work.department_name, work.employee_name, work.position_name, work.title,
    work.start_time, work.end_time, round(minutes(work) / 60),
  ]);
  const csv = `\uFEFF${[columns, ...records].map((row) => row.map(escapeCsv).join(";")).join("\r\n")}`;
  const blobUrl = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = blobUrl;
  anchor.download = `BTL_занятость_${$("from").value}_${$("to").value}.csv`;
  anchor.click();
  URL.revokeObjectURL(blobUrl);
});

$("password-open").addEventListener("click", () => $("password-dialog").showModal());
$("password-cancel").addEventListener("click", () => $("password-dialog").close());
$("password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const newPassword = $("new-password").value;
  if (newPassword.length < 10 || (newPassword.match(/[A-Z]/g) || []).length < 2
      || (newPassword.match(/[a-z]/g) || []).length < 2) {
    notify("Пароль: минимум 10 символов, 2 заглавные и 2 строчные латинские буквы.", true);
    return;
  }
  try {
    const result = await api("/api/password", {
      method: "POST",
      body: JSON.stringify({
        old_password: $("old-password").value,
        new_password: newPassword,
      }),
    });
    csrfToken = result.csrf_token;
    $("password-form").reset();
    $("password-dialog").close();
    notify("Пароль обновлён. Остальные сессии завершены.");
  } catch (error) {
    displayError(error);
  }
});

async function start() {
  try {
    await updateSession();
  } catch (error) {
    $("login-error").textContent = error.message;
  }
}

start();
