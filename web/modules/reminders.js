// Reminders & deadlines (F4): a dashboard card listing manual follow-up
// reminders + automatic "stale application" nudges, a nav badge with the total
// count, and the reminder editor injected into the job-detail panel.
import { api, escapeHtml, showToast } from "./helpers.js";
import { t } from "./i18n.js";

let _onOpenJob = null;

function _fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString();
}

function _daysSince(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return Math.floor((Date.now() - d.getTime()) / 86400000);
}

export async function loadReminders() {
  const section = document.getElementById("remindersSection");
  const badge = document.getElementById("remindersNavBadge");
  if (!section) return;
  let data;
  try {
    data = await api("/api/reminders");
  } catch {
    section.style.display = "none";
    if (badge) badge.classList.add("hidden");
    return;
  }
  const reminders = data.reminders || [];
  const stale = data.stale || [];
  const count = typeof data.count === "number" ? data.count : reminders.length + stale.length;

  if (badge) {
    badge.textContent = String(count);
    badge.classList.toggle("hidden", count === 0);
  }

  const list = document.getElementById("remindersList");
  if (!list) return;
  if (!count) {
    section.style.display = "none";
    list.innerHTML = "";
    return;
  }
  section.style.display = "block";

  const rows = [];
  for (const r of reminders) {
    const cls = r.overdue ? "reminder-item overdue" : "reminder-item";
    const sub = escapeHtml(r.azienda || "") + (r.note ? ` · ${escapeHtml(r.note)}` : "");
    rows.push(
      `<button type="button" class="${cls}" data-job="${r.job_id}">` +
        `<span class="material-symbols-outlined">event</span>` +
        `<span class="reminder-main"><strong>${escapeHtml(r.titolo || "?")}</strong>` +
        `<span class="reminder-sub">${sub}</span></span>` +
        `<span class="reminder-when">${r.overdue ? "⚠ " : ""}${escapeHtml(_fmtDate(r.due_at))}</span>` +
        `</button>`,
    );
  }
  for (const s of stale) {
    const days = _daysSince(s.since);
    const when = days != null ? t("reminders.stuckDays", { days }) : "";
    rows.push(
      `<button type="button" class="reminder-item stale" data-job="${s.job_id}">` +
        `<span class="material-symbols-outlined">hourglass_bottom</span>` +
        `<span class="reminder-main"><strong>${escapeHtml(s.titolo || "?")}</strong>` +
        `<span class="reminder-sub">${escapeHtml(s.azienda || "")} · ${escapeHtml(
          t("jobs.status." + (s.status || "applied")),
        )}</span></span>` +
        `<span class="reminder-when">${escapeHtml(when)}</span>` +
        `</button>`,
    );
  }
  list.innerHTML = rows.join("");
  list.querySelectorAll("[data-job]").forEach((el) => {
    el.addEventListener("click", () => {
      const id = parseInt(el.dataset.job, 10);
      if (id && typeof _onOpenJob === "function") _onOpenJob(id);
    });
  });
}

// HTML for the reminder editor embedded in the job-detail timeline card.
export function reminderEditorHtml(job) {
  const value = job && job.reminder_at ? String(job.reminder_at).slice(0, 10) : "";
  const note = job && job.reminder_note ? String(job.reminder_note) : "";
  return `
    <div class="reminder-editor">
      <span class="reminder-editor-label">${t("reminders.setLabel")}</span>
      <div class="reminder-editor-row">
        <input type="date" id="detailReminderDate" value="${escapeHtml(value)}" />
        <input type="text" id="detailReminderNote" value="${escapeHtml(note)}" data-i18n-placeholder="reminders.notePlaceholder" placeholder="Note (optional)" />
        <button type="button" id="detailReminderSave" class="ghost-btn small">${t("reminders.save")}</button>
        <button type="button" id="detailReminderClear" class="ghost-btn small">${t("reminders.clear")}</button>
      </div>
    </div>`;
}

// Attach save/clear handlers to the editor injected by reminderEditorHtml.
export function wireReminderEditor(jobId) {
  const saveBtn = document.getElementById("detailReminderSave");
  const clearBtn = document.getElementById("detailReminderClear");
  const dateEl = document.getElementById("detailReminderDate");
  const noteEl = document.getElementById("detailReminderNote");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      try {
        await api(`/api/jobs/${jobId}/reminder`, {
          method: "POST",
          body: JSON.stringify({
            reminder_at: dateEl ? dateEl.value : "",
            note: noteEl ? noteEl.value : "",
          }),
        });
        showToast(t("reminders.saved"), "info");
        loadReminders();
      } catch (err) {
        showToast(`${t("toast.actionError")}: ${err.message}`, "error");
      }
    });
  }
  if (clearBtn) {
    clearBtn.addEventListener("click", async () => {
      try {
        await api(`/api/jobs/${jobId}/reminder`, { method: "DELETE" });
        if (dateEl) dateEl.value = "";
        if (noteEl) noteEl.value = "";
        showToast(t("reminders.cleared"), "info");
        loadReminders();
      } catch (err) {
        showToast(`${t("toast.actionError")}: ${err.message}`, "error");
      }
    });
  }
}

export function initReminders({ onOpenJob } = {}) {
  _onOpenJob = onOpenJob || null;
  const refreshBtn = document.getElementById("refreshRemindersBtn");
  if (refreshBtn) refreshBtn.addEventListener("click", loadReminders);
}
