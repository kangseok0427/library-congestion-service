// Separate administrator tools; Excel publishing and the visitor layout stay intact.
(() => {
  const el = id => document.getElementById(id);
  const today = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Asia/Seoul'}).format(new Date());
  let month = today.slice(0, 7), selected = null, dates = null, saving = false;
  let generation = 0;
  const label = day => `${Number(day.slice(0, 4))}년 ${Number(day.slice(5, 7))}월 ${Number(day.slice(8))}일`;
  const monday = day => new Date(day + 'T00:00:00Z').getUTCDay() === 1;
  const error = (id, e) => {
    if (!handleAuthError(e)) el(id).textContent = messageFor(e);
  };

  async function loadTraffic() {
    const current = generation;
    el('traffic-status').textContent = '집계를 불러오는 중입니다.';
    el('reload-traffic').disabled = true;
    try {
      const data = await adminApi.traffic();
      if (current !== generation) return;
      for (const [id, key] of [['today-visitors','today_visitors'], ['total-visitors','total_visitors'],
        ['today-page-views','today_page_views'], ['total-page-views','total_page_views']]) {
        el(id).textContent = Number(data[key]).toLocaleString('ko-KR');
      }
      el('traffic-status').textContent = data.date ? `${data.date} 집계` : '집계를 불러왔습니다.';
    } catch (e) { if (current === generation) error('traffic-status', e); }
    finally { el('reload-traffic').disabled = false; }
  }

  function render() {
    el('calendar-month').textContent = `${Number(month.slice(0,4))}년 ${Number(month.slice(5))}월`;
    const grid = el('closure-calendar');
    grid.replaceChildren();
    const [y, m] = month.split('-').map(Number);
    const offset = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
    const count = new Date(Date.UTC(y, m, 0)).getUTCDate();
    for (let i = 0; i < offset; i++) grid.append(document.createElement('span'));
    for (let n = 1; n <= count; n++) {
      const day = `${month}-${String(n).padStart(2, '0')}`;
      const registered = dates?.find(d => d.date === day);
      const closed = !!registered || monday(day);
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = n;
      button.dataset.date = day;
      button.className = 'calendar-day' + (registered ? ' registered' : monday(day) ? ' regular' : '');
      button.setAttribute('aria-label', `${label(day)}${closed ? ' 휴관일' : ''}`);
      button.setAttribute('aria-pressed', String(day === selected));
      if (day === today) button.setAttribute('aria-current', 'date');
      button.disabled = !dates || saving;
      button.addEventListener('click', () => {
        selected = day;
        el('closure-reason').value = registered?.reason || '';
        el('closure-status').textContent = '';
        render();
      });
      grid.append(button);
    }
    const registered = dates?.find(d => d.date === selected);
    el('closure-selected').textContent = selected
      ? `${label(selected)} · ${monday(selected) ? '월요일 정기 휴관' : registered ? '등록된 휴관일' : '휴관일 미등록'}`
      : '달력에서 날짜를 선택하세요.';
    el('closure-reason').disabled = saving || !dates || !selected || monday(selected);
    el('save-closure').disabled = el('closure-reason').disabled;
    el('save-closure').textContent = registered ? '사유 수정' : '휴관일 등록';
    el('remove-closure').disabled = saving || !registered;
    el('calendar-prev').disabled = saving;
    el('calendar-next').disabled = saving;
  }

  async function loadClosures() {
    const current = generation;
    dates = null; render();
    el('closure-status').textContent = '휴관일을 불러오는 중입니다.';
    try {
      const data = await adminApi.closures();
      if (current !== generation) return;
      dates = data.closed_dates;
      el('closure-status').textContent = '';
      render();
    } catch (e) { if (current === generation) error('closure-status', e); }
  }

  async function save(remove = false) {
    if (!selected || saving || !dates) return;
    saving = true; render();
    el('closure-status').textContent = '저장하는 중입니다.';
    try {
      const day = selected, reason = el('closure-reason').value.trim();
      if (remove) await adminApi.removeClosure(day);
      else await adminApi.setClosure(day, reason);
      dates = dates.filter(d => d.date !== day);
      if (!remove) dates.push({date: day, reason: reason || '휴관일'});
      el('closure-status').textContent = remove
        ? `${label(day)}의 휴관일 등록을 해제했습니다.${monday(day) ? ' 월요일 정기 휴관은 유지됩니다.' : ''}`
        : `${label(day)}을 휴관일로 저장했습니다.`;
      el('closure-reason').value = remove ? '' : reason;
    } catch (e) { error('closure-status', e); }
    finally { saving = false; render(); }
  }

  function moveMonth(offset) {
    const [y, m] = month.split('-').map(Number);
    month = new Date(Date.UTC(y, m - 1 + offset, 1)).toISOString().slice(0,7);
    selected = null; el('closure-reason').value = ''; render();
  }
  el('calendar-prev').addEventListener('click', () => moveMonth(-1));
  el('calendar-next').addEventListener('click', () => moveMonth(1));
  el('reload-traffic').addEventListener('click', loadTraffic);
  el('closure-form').addEventListener('submit', e => {e.preventDefault(); save();});
  el('remove-closure').addEventListener('click', () => save(true));
  window.loadAdminOperations = () => {
    generation++;
    selected = null;
    for (const id of ['today-visitors','total-visitors','today-page-views','total-page-views']) el(id).textContent = '—';
    loadTraffic(); loadClosures();
  };
})();
