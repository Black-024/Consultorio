"use strict";

const $ = (sel) => document.querySelector(sel);

/* Crea elementos sin innerHTML (los nombres de pacientes se insertan siempre como texto). */
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === false || value == null) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch (_) { /* sin cuerpo */ }
  if (!res.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : "Revisa los datos ingresados.";
    throw new Error(detail);
  }
  return data;
}

/* ------------------------------------------------------------------ etiquetas */
const STATUS = { pending: "Pendiente", confirmed: "Confirmada", cancelled: "Cancelada" };
const REMINDER = { pending: "programado", sent: "enviado", failed: "no se pudo enviar", skipped: "no corresponde" };
const ATTENTION = {
  not_on_whatsapp: "Este número no tiene WhatsApp. Avisa al paciente por otro medio.",
  send_failed: "No se pudo enviar el mensaje. Avisa al paciente por otro medio.",
  no_response: "El paciente no respondió en 5 días. Decide cómo seguir.",
};
const TOAST_KIND = { confirmed: "ok", cancelled: "warn" };

function formatWhen(local) {
  const [d, t] = local.split("T");
  const [y, m, day] = d.split("-").map(Number);
  const [h, min] = t.split(":").map(Number);
  const text = new Date(y, m - 1, day, h, min).toLocaleDateString("es-AR", {
    weekday: "long", day: "numeric", month: "long",
  });
  return `${text.charAt(0).toUpperCase()}${text.slice(1)} · ${t} h`;
}

/* ------------------------------------------------------------------ avisos emergentes */
const toasts = $("#toasts");

function enforceFit() {
  // Si no entran todos, se cierran los más antiguos (los de abajo) hasta que entren.
  while (toasts.children.length > 1 && toasts.scrollHeight > toasts.clientHeight) {
    toasts.lastElementChild.remove();
  }
}

function toast(title, body, kind = "info") {
  let timer = null;
  const close = () => { clearTimeout(timer); node.remove(); };
  const more = el("button", { class: "toast-more", type: "button" }, "Ver más");
  const node = el("div", { class: `toast toast-${kind}`, role: "status" },
    el("div", { class: "toast-head" },
      el("span", { class: "toast-title" }, title),
      el("button", { class: "toast-close", type: "button", "aria-label": "Cerrar aviso", onclick: close }, "✕")),
    el("div", { class: "toast-body" }, body),
    more);
  more.addEventListener("click", () => {
    const open = node.classList.toggle("is-open");
    more.textContent = open ? "Ver menos" : "Ver más";
    if (open) clearTimeout(timer);            // mientras lo lee, no se cierra solo
    else timer = setTimeout(close, 5000);
    enforceFit();
  });
  timer = setTimeout(close, 5000);
  toasts.prepend(node);
  enforceFit();
}

/* ------------------------------------------------------------------ citas */
let view = "upcoming";

function chip(text, cls) { return el("span", { class: `chip ${cls}` }, text); }

async function setStatus(id, status, question) {
  if (question && !window.confirm(question)) return;
  try {
    await api(`/api/appointments/${id}/status`, { method: "POST", body: JSON.stringify({ status }) });
    await loadAppointments();
  } catch (err) { toast("No se pudo actualizar", err.message, "alert"); }
}

function appointmentCard(a) {
  const actions = [];
  if (a.status === "pending") {
    actions.push(el("button", { class: "btn btn-secondary btn-small", type: "button",
      onclick: () => setStatus(a.id, "confirmed") }, "Marcar como confirmada"));
  }
  if (a.status !== "cancelled") {
    actions.push(el("button", { class: "btn btn-danger btn-small", type: "button",
      onclick: () => setStatus(a.id, "cancelled", `¿Cancelar la cita de ${a.patient_name}?`) }, "Cancelar cita"));
  }
  return el("article", { class: `appt${a.needs_attention ? " appt-alert" : ""}` },
    el("div", {},
      el("p", { class: "appt-name" }, a.patient_name),
      el("p", { class: "appt-when" }, formatWhen(a.starts_at_local)),
      el("p", { class: "appt-phone" }, a.phone)),
    el("div", { class: "appt-meta" },
      chip(STATUS[a.status], `status-${a.status}`),
      el("p", { class: "meta-line" }, `Aviso de 7 días: ${REMINDER[a.reminder_week]}`),
      el("p", { class: "meta-line" }, `Aviso de 1 día: ${REMINDER[a.reminder_day]}`),
      a.calendar_synced === null ? null
        : el("p", { class: "meta-line" }, `Google Calendar: ${a.calendar_synced ? "agendada" : "pendiente"}`)),
    a.needs_attention && a.status !== "cancelled"
      ? el("p", { class: "appt-note" }, ATTENTION[a.attention_reason] || "Requiere atención.") : null,
    actions.length ? el("div", { class: "appt-actions" }, actions) : null);
}

async function loadAppointments() {
  const items = await api(`/api/appointments?include_past=${view === "all"}`);
  const urgent = items.filter((a) => a.needs_attention && a.status !== "cancelled");
  const rest = items.filter((a) => !urgent.includes(a));
  $("#attention").hidden = urgent.length === 0;
  $("#attention-list").replaceChildren(...urgent.map(appointmentCard));
  $("#list").replaceChildren(...rest.map(appointmentCard));
  $("#empty").hidden = items.length > 0;
}

document.querySelectorAll(".tabs button").forEach((btn) =>
  btn.addEventListener("click", () => {
    view = btn.dataset.view;
    document.querySelectorAll(".tabs button").forEach((b) => {
      const active = b === btn;
      b.classList.toggle("is-active", active);
      b.setAttribute("aria-pressed", String(active));
    });
    loadAppointments().catch((e) => toast("Error", e.message, "alert"));
  }));

/* ------------------------------------------------------------------ formulario */
const form = $("#form");
const formError = $("#form-error");
const today = new Date();
$("#date").min = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;
  const name = $("#patient").value.trim();
  const phone = $("#phone").value.trim();
  const date = $("#date").value;
  const time = $("#time").value;
  if (!name || !phone || !date || !time) {
    formError.textContent = "Completa el nombre, el teléfono, la fecha y la hora.";
    formError.hidden = false;
    return;
  }
  try {
    const appt = await api("/api/appointments", {
      method: "POST",
      body: JSON.stringify({ patient_name: name, phone, starts_at: `${date}T${time}` }),
    });
    form.reset();
    toast("Cita agendada", `${appt.patient_name} · ${formatWhen(appt.starts_at_local)}`, "ok");
    await loadAppointments();
    await pollNotifications();
  } catch (err) {
    formError.textContent = err.message;
    formError.hidden = false;
  }
});

/* ------------------------------------------------------------------ panel de notificaciones */
const panel = $("#panel");
let lastSeenId = null;

function setPanel(open) {
  panel.hidden = !open;
  $("#bell").setAttribute("aria-expanded", String(open));
  if (open) $("#panel-close").focus(); else $("#bell").focus();
}
$("#bell").addEventListener("click", () => setPanel(panel.hidden));
$("#panel-close").addEventListener("click", () => setPanel(false));
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) setPanel(false); });

async function removeNotification(id) {
  try { await api(`/api/notifications/${id}`, { method: "DELETE" }); await pollNotifications(); }
  catch (err) { toast("No se pudo eliminar", err.message, "alert"); }
}
$("#clear-all").addEventListener("click", async () => {
  if (!window.confirm("¿Eliminar todas las notificaciones? Esta acción no se puede deshacer.")) return;
  try { await api("/api/notifications", { method: "DELETE" }); await pollNotifications(); }
  catch (err) { toast("No se pudieron eliminar", err.message, "alert"); }
});

function renderNotifications(items) {
  const badge = $("#badge");
  badge.textContent = items.length;
  badge.hidden = items.length === 0;
  $("#notif-empty").hidden = items.length > 0;
  $("#clear-all").hidden = items.length === 0;
  $("#notif-list").replaceChildren(...[...items].reverse().map((n) =>
    el("article", { class: "notif" },
      el("h3", {}, n.title),
      el("time", { datetime: n.created_at }, new Date(n.created_at).toLocaleString("es-AR")),
      el("p", {}, n.body),
      el("button", { class: "btn btn-secondary btn-small", type: "button",
        onclick: () => removeNotification(n.id) }, "Eliminar"))));
}

async function pollNotifications() {
  const items = await api("/api/notifications");
  renderNotifications(items);
  const newest = items.reduce((max, n) => Math.max(max, n.id), 0);
  if (lastSeenId !== null) {
    const fresh = items.filter((n) => n.id > lastSeenId);
    fresh.forEach((n) => toast(n.title, n.body, TOAST_KIND[n.kind] || "alert"));
    if (fresh.length) loadAppointments().catch(() => {});
  }
  lastSeenId = Math.max(lastSeenId ?? 0, newest);
}

/* ------------------------------------------------------------------ arranque */
loadAppointments().catch((e) => toast("Error", e.message, "alert"));
pollNotifications().catch(() => {});
setInterval(() => pollNotifications().catch(() => {}), 5000);
setInterval(() => loadAppointments().catch(() => {}), 30000);
