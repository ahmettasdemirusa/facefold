/* Facefold UI glue: job polling, face selection, live rule preview, deleting. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

async function post(url, body) {
  const opts = { method: 'POST' };
  if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  try { return await res.json(); } catch (e) { return { ok: res.ok }; }
}

/* Interface text. The sentences live in facefold/i18n.py; base.html hands the
   "js." ones to the page as window.FACEFOLD_T before this file loads. A key
   with no sentence behind it comes back as itself - obvious on screen, and it
   never throws in the middle of a click. Called `T` rather than `t` because a
   local `t` (the clicked element) is already in scope further down. */
function T(key, vars) {
  var s = (window.FACEFOLD_T || {})[key] || key;
  if (vars) {
    for (var k in vars) {
      s = s.replace('{' + k + '}', function () { return vars[k]; });
    }
  }
  return s;
}

/* Thousands separators follow the interface language, not the machine's. */
const tr = (n) =>
  Number(n || 0).toLocaleString(document.documentElement.lang || 'tr-TR');

/* ------------------------------------------------------------------ jobs */

let pollTimer = null;

function renderJob(job) {
  const box = $('#job-box');
  if (!box) return;

  if (!job || job.state !== 'running') {
    if (box.dataset.was === 'running') location.reload();
    box.hidden = true;
    box.dataset.was = '';
    stopPolling();
    return;
  }

  box.hidden = false;
  box.dataset.was = 'running';
  $('#job-msg').textContent = job.message || job.kind;

  const total = job.total || 0;
  const bar = $('#job-bar');
  if (total > 0) {
    bar.classList.remove('idle');
    $('#job-fill').style.width = job.percent + '%';
    $('#job-detail').textContent =
      tr(job.done) + ' / ' + tr(total) + '  ·  %' + job.percent +
      (job.detail ? '  ·  ' + job.detail : '');
  } else {
    bar.classList.add('idle');
    $('#job-detail').textContent = job.detail || T('js.working');
  }
}

async function poll() {
  try {
    const data = await (await fetch('/api/durum')).json();
    renderJob(data.job);
  } catch (e) { /* server restarting; try again next tick */ }
}

function startPolling() {
  if (pollTimer) return;
  poll();
  pollTimer = setInterval(poll, 1200);
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function runJob(kind, btn) {
  if (btn) btn.disabled = true;
  const res = await post('/api/is/' + kind);
  if (!res.ok) {
    alert(res.message || res.error || T('js.job_start_failed'));
    if (btn) btn.disabled = false;
    return;
  }
  startPolling();
}

/* --------------------------------------------------------- face selection */

/* Faces live in containers marked `data-group="name"`, so several independent
   selections can share one page (suggestions vs. confirmed, for example). */
function groupBox(name) {
  return $('.faces[data-group="' + name + '"]') || document;
}

function picked(name) {
  return $$('input:checked', groupBox(name)).map(el => parseInt(el.value, 10));
}

function setAll(name, on) {
  $$('.face-pick', groupBox(name)).forEach(label => {
    const cb = $('input', label);
    cb.checked = on;
    label.classList.toggle('sel', on);
  });
  refreshSelection();
}

/* Photo selection is kept in a Set rather than read off the checkboxes,
   because "select all" spans pages that are not in the DOM. Visible tiles are
   mirrored into it both ways. */
const photoSelection = new Set();

function pickedPhotos() {
  return Array.from(photoSelection);
}

function syncPhotoTiles() {
  $$('.shot-pick').forEach(tile => {
    const cb = $('input', tile);
    if (!cb) return;
    const on = photoSelection.has(parseInt(cb.value, 10));
    cb.checked = on;
    tile.classList.toggle('sel', on);
  });
}

function setPhotoPage(on) {
  $$('.shot-pick input').forEach(cb => {
    const id = parseInt(cb.value, 10);
    if (on) photoSelection.add(id); else photoSelection.delete(id);
  });
  syncPhotoTiles();
  refreshSelection();
}

function refreshSelection() {
  const n = $$('.face-pick input:checked').length;
  const bar = $('#sel-bar');
  if (bar) bar.hidden = n === 0;
  const label = $('#sel-count');
  if (label) label.textContent = T('js.faces_selected', { n: n });
  const shots = $('#shot-count');
  if (shots) shots.textContent = T('js.photos_selected', { n: pickedPhotos().length });
  $$('[data-count-for]').forEach(el => {
    el.textContent = picked(el.dataset.countFor).length;
  });
}

/* ---------------------------------------------------------------- deleting */

async function deletePhotos(payload, describe) {
  const pre = await post('/api/fotograf/sil', { ...payload, preview: true });
  if (!pre.ok) { alert(pre.error || T('js.delete_failed')); return; }

  const p = pre.preview;
  const mb = (p.bytes / 1048576).toFixed(1);
  const message =
    describe + '\n\n' +
    T('js.delete_count', { n: tr(p.photos), mb: mb }) + '\n' +
    (p.links ? T('js.delete_links', { n: tr(p.links) }) + '\n' : '') +
    '\n' + T('js.delete_warning');

  if (!confirm(message)) return;

  const res = await post('/api/fotograf/sil', payload);
  if (!res.ok) { alert(res.error || T('js.delete_failed')); return; }
  alert(T('js.deleted_ok', { n: tr(res.deleted) }) +
        (res.failed ? '\n' + T('js.delete_failed_count', { n: res.failed }) : ''));
  location.reload();
}

/* ------------------------------------------------------------------ boot */

document.addEventListener('DOMContentLoaded', () => {
  const box = $('#job-box');
  if (box && !box.hidden) startPolling();
  refreshSelection();

  document.addEventListener('click', async (ev) => {
    const t = ev.target;

    const job = t.closest('[data-job]');
    if (job) { ev.preventDefault(); runJob(job.dataset.job, job); return; }

    const stop = t.closest('[data-stop]');
    if (stop) { ev.preventDefault(); await post('/api/is/durdur'); return; }

    const open = t.closest('[data-open]');
    if (open) {
      ev.preventDefault();
      const res = await post('/api/klasor-ac', { path: open.dataset.open });
      if (!res.ok) alert(res.error || T('js.open_folder_failed'));
      return;
    }

    const merge = t.closest('[data-merge-keep]');
    if (merge) {
      ev.preventDefault();
      if (!confirm(merge.dataset.mergeLabel + '\n\n' + T('js.merge_confirm'))) return;
      const res = await post('/api/kisi/birlestirme-onerisi',
                             { keep_id: merge.dataset.mergeKeep,
                               drop_id: merge.dataset.mergeDrop });
      if (!res.ok) { alert(res.error || T('js.merge_failed')); return; }
      location.reload();
      return;
    }

    const tail = t.closest('[data-ignore-tail]');
    if (tail) {
      ev.preventDefault();
      const res = await post('/api/gruplar/kuyrugu-gizle',
                             { below: tail.dataset.ignoreTail });
      if (!res.ok) { alert(T('js.hide_failed')); return; }
      alert(T('js.tail_hidden', { n: tr(res.hidden) }));
      location.reload();
      return;
    }

    const unignore = t.closest('[data-unignore]');
    if (unignore) {
      ev.preventDefault();
      await post('/api/gruplar/gizlileri-goster');
      location.reload();
      return;
    }

    const repair = t.closest('[data-repair-dates]');
    if (repair) {
      ev.preventDefault();
      repair.disabled = true;
      repair.textContent = T('js.repairing');
      const res = await post('/api/tarihleri-onar');
      if (!res.ok) { alert(T('js.repair_failed')); repair.disabled = false; return; }
      alert(T('js.dates_repaired', { n: tr(res.cleared),
                                     f: tr(res.from_filename),
                                     g: tr(res.takeout) }));
      location.reload();
      return;
    }

    const hide = t.closest('[data-hide-group]');
    if (hide) {
      ev.preventDefault();
      await post('/api/grup/' + hide.dataset.hideGroup + '/gizle');
      const card = hide.closest('.group-card');
      if (card) card.remove();
      return;
    }

    /* ---- bulk selection helpers ---- */
    const all = t.closest('[data-pick-all]');
    if (all) {
      ev.preventDefault();
      if (all.dataset.pickAll === 'shots') setPhotoPage(true);
      else setAll(all.dataset.pickAll, true);
      return;
    }

    const none = t.closest('[data-pick-none]');
    if (none) {
      ev.preventDefault();
      if (none.dataset.pickNone === 'shots') { photoSelection.clear(); syncPhotoTiles(); refreshSelection(); }
      else setAll(none.dataset.pickNone, false);
      return;
    }

    const every = t.closest('#pick-every');
    if (every) {
      ev.preventDefault();
      const was = every.textContent;
      every.disabled = true;
      every.textContent = T('js.loading');
      const res = await (await fetch(every.dataset.url)).json();
      every.disabled = false;
      every.textContent = was;
      if (!res.ok) { alert(T('js.list_failed')); return; }
      photoSelection.clear();
      res.ids.forEach(id => photoSelection.add(id));
      syncPhotoTiles();
      refreshSelection();
      return;
    }

    /* ---- attach selected photos to a person ---- */
    const assignGo = t.closest('#assign-go');
    if (assignGo) {
      ev.preventDefault();
      const ids = pickedPhotos();
      if (!ids.length) { alert(T('js.pick_photos_first')); return; }
      const select = $('#assign-person');
      const payload = { photo_ids: ids };
      if (select.value === '__new__') {
        const name = prompt(T('js.new_person_name'));
        if (!name || !name.trim()) return;
        payload.new_name = name.trim();
      } else if (select.value) {
        payload.person_id = select.value;
      } else {
        alert(T('js.pick_person_first'));
        return;
      }
      if (!confirm(T('js.assign_confirm', {
            n: tr(ids.length),
            name: payload.new_name || select.options[select.selectedIndex].text,
          }))) return;
      assignGo.disabled = true;
      const res = await post('/api/fotograf/kisi-ata', payload);
      assignGo.disabled = false;
      if (!res.ok) { alert(res.error || T('js.assign_failed')); return; }
      alert(T('js.assign_done', { n: tr(res.count), name: res.name }) +
            (res.attached ? '\n' + T('js.assign_faces', { n: res.attached }) : '') +
            (res.grown ? '\n' + T('js.assign_grown', { n: res.grown }) : ''));
      location.reload();
      return;
    }

    /* ---- re-run recognition ---- */
    const matchNow = t.closest('#match-now');
    if (matchNow) {
      ev.preventDefault();
      const was = matchNow.textContent;
      matchNow.disabled = true;
      matchNow.textContent = T('js.searching');
      const res = await post('/api/eslestir');
      matchNow.disabled = false;
      matchNow.textContent = was;
      if (!res.ok) { alert(res.error || T('js.match_failed')); return; }
      if (res.assigned) {
        alert(T('js.match_found', { n: tr(res.assigned), checked: tr(res.checked) }));
        location.reload();
      } else {
        alert(T('js.match_none', { n: tr(res.checked) }));
      }
      return;
    }

    /* ---- assigning faces ---- */
    const assign = t.closest('[data-assign-group]');
    if (assign) {
      ev.preventDefault();
      const ids = picked(assign.dataset.assignGroup);
      if (!ids.length) { alert(T('js.pick_faces_first')); return; }
      await post('/api/yuz/ata', { face_ids: ids, person_id: assign.dataset.person });
      location.reload();
      return;
    }

    const detach = t.closest('[data-detach-group]');
    if (detach) {
      ev.preventDefault();
      const ids = picked(detach.dataset.detachGroup);
      if (!ids.length) { alert(T('js.pick_faces_first')); return; }
      if (!confirm(T('js.detach_confirm', { n: ids.length }))) return;
      await post('/api/yuz/ata', { face_ids: ids, person_id: null });
      location.reload();
      return;
    }

    const move = t.closest('[data-move-group]');
    if (move) {
      ev.preventDefault();
      const name = move.dataset.moveGroup;
      const ids = picked(name);
      const select = $('[data-move-select="' + name + '"]');
      if (!ids.length) { alert(T('js.pick_faces_first')); return; }
      if (!select || !select.value) { alert(T('js.pick_person_first')); return; }
      await post('/api/yuz/ata', { face_ids: ids, person_id: select.value });
      location.reload();
      return;
    }

    const create = t.closest('[data-create-person]');
    if (create) {
      ev.preventDefault();
      const name = create.dataset.createPerson;
      const ids = picked(name);
      const input = $('[data-new-person="' + name + '"]');
      if (!ids.length) { alert(T('js.pick_faces_first')); return; }
      if (!input || !input.value.trim()) { alert(T('js.type_name_first')); return; }
      const res = await post('/api/kisi/olustur',
                             { name: input.value, face_ids: ids });
      if (!res.ok) { alert(res.error || T('js.create_failed')); return; }
      if (res.grown) {
        alert(T('js.created_grown', { n: res.count, m: res.grown }));
      }
      location.reload();
      return;
    }

    /* ---- deleting ---- */
    const delPerson = t.closest('[data-delete-person]');
    if (delPerson) {
      ev.preventDefault();
      deletePhotos({ which: 'person', person_id: delPerson.dataset.deletePerson },
                   T('js.delete_person', { name: delPerson.dataset.name }));
      return;
    }

    const delWhich = t.closest('[data-delete-which]');
    if (delWhich) {
      ev.preventDefault();
      deletePhotos({ which: delWhich.dataset.deleteWhich },
                   delWhich.dataset.describe || T('js.delete_selected'));
      return;
    }

    const delPicked = t.closest('[data-delete-picked]');
    if (delPicked) {
      ev.preventDefault();
      const ids = pickedPhotos();
      if (!ids.length) { alert(T('js.pick_photos_first')); return; }
      deletePhotos({ photo_ids: ids }, T('js.delete_picked'));
      return;
    }

    /* ---- Google Takeout ---- */
    const takeout = t.closest('[data-takeout]');
    if (takeout) {
      ev.preventDefault();
      takeout.disabled = true;
      takeout.textContent = T('js.importing');
      const res = await post('/api/takeout/aktar');
      if (!res.ok) { alert(res.error || T('js.import_failed')); takeout.disabled = false; return; }
      alert(T('js.takeout_done', { p: res.persons,
                                   s: res.seeded,
                                   a: res.assigned,
                                   d: res.dates }));
      location.reload();
      return;
    }

    /* ---- clicking a face card toggles it ---- */
    const pick = t.closest('.face-pick, .shot-pick');
    if (pick && t.tagName !== 'INPUT' && t.tagName !== 'A') {
      const cb = $('input', pick);
      if (cb) {
        cb.checked = !cb.checked;
        pick.classList.toggle('sel', cb.checked);
        if (pick.classList.contains('shot-pick')) {
          const id = parseInt(cb.value, 10);
          if (cb.checked) photoSelection.add(id); else photoSelection.delete(id);
        }
        refreshSelection();
      }
    }
  });

  document.addEventListener('change', (ev) => {
    if (ev.target.matches('.face-pick input, .shot-pick input')) {
      const tile = ev.target.closest('.face-pick, .shot-pick');
      tile.classList.toggle('sel', ev.target.checked);
      if (tile.classList.contains('shot-pick')) {
        const id = parseInt(ev.target.value, 10);
        if (ev.target.checked) photoSelection.add(id); else photoSelection.delete(id);
      }
      refreshSelection();
    }
  });

  /* legacy single-selection bar (group and photo pages) */
  const assign = $('#sel-assign');
  if (assign) {
    assign.addEventListener('click', async () => {
      const pid = $('#sel-person').value;
      const ids = $$('.face-pick input:checked').map(el => parseInt(el.value, 10));
      if (!ids.length) return;
      await post('/api/yuz/ata', { face_ids: ids, person_id: pid || null });
      location.reload();
    });
  }

  const detach = $('#sel-detach');
  if (detach) {
    detach.addEventListener('click', async () => {
      const ids = $$('.face-pick input:checked').map(el => parseInt(el.value, 10));
      if (!ids.length) return;
      await post('/api/yuz/ata', { face_ids: ids, person_id: null });
      location.reload();
    });
  }

  /* live rule preview -------------------------------------------------- */
  const form = $('#rule-form');
  if (form) {
    const out = $('#rule-count');
    let timer = null;

    const collect = () => ({
      rule_type: $('[name=rule_type]', form).value,
      people: $$('[name=people]:checked', form).map(el => parseInt(el.value, 10)),
      options: {
        ignore_unknown: $('[name=ignore_unknown]', form).checked,
        include_duplicates: $('[name=include_duplicates]', form).checked,
        min_face_px: parseInt($('[name=min_face_px]', form).value || '0', 10),
        date_from: $('[name=date_from]', form).value,
        date_to: $('[name=date_to]', form).value,
      },
    });

    const update = async () => {
      const payload = collect();
      const needsPeople = ['solo', 'exact', 'contains', 'any'].includes(payload.rule_type);
      $('#people-box').style.opacity = needsPeople ? '1' : '.4';
      if (needsPeople && !payload.people.length) {
        out.textContent = T('js.choose_person');
        out.className = 'tag-chip';
        return;
      }
      out.textContent = '...';
      const res = await post('/api/kategori/onizleme', payload);
      if (res.ok) {
        out.textContent = T('js.photo_count', { n: tr(res.count) });
        out.className = 'tag-chip ' + (res.count ? 'on' : 'off');
      }
    };

    form.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(update, 250);
    });
    form.addEventListener('change', () => { clearTimeout(timer); timer = setTimeout(update, 60); });
    update();

    $$('[data-suggest]').forEach(btn => {
      btn.addEventListener('click', () => {
        const s = JSON.parse(btn.dataset.suggest);
        $('[name=name]', form).value = s.name;
        $('[name=rule_type]', form).value = s.rule_type;
        $$('[name=people]', form).forEach(cb => {
          cb.checked = s.people.includes(parseInt(cb.value, 10));
        });
        form.scrollIntoView({ behavior: 'smooth', block: 'center' });
        update();
      });
    });
  }
});
