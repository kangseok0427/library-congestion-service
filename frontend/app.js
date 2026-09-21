// T05 (ADE-9) 이용자 웹. 데이터는 api.js를 통해서만 받습니다.
const $ = id => document.getElementById(id);
const LABEL = { quiet: '여유', normal: '보통', busy: '혼잡' };
const METHOD = { same_weekday_hour: '같은 요일·시간 평균', same_hour_fallback: '같은 시간 평균 (요일 자료 부족)', unavailable: '자료 부족' };
let generation = 0;

const kstNow = () => new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Seoul' }));
const kstDate = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
const fmtTime = iso => { const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }); };
const koDate = ymd => { const [y, m, d] = ymd.split('-'); const w = '일월화수목금토'[new Date(`${ymd}T00:00:00+09:00`).getDay()]; return `${Number(m)}월 ${Number(d)}일 (${w})`; };

function clear() {
  $('hourly').replaceChildren(); $('chart').replaceChildren();
  $('now').dataset.level = ''; $('now-when').textContent = '불러오는 중'; $('level').textContent = '—'; $('level').classList.remove('small');
  $('best').textContent = '—'; $('recommendation').textContent = '';
  $('detail').textContent = '시간대를 선택하세요.';
  $('actual').textContent = '불러오는 중'; $('actual-note').textContent = '';
  $('basis').textContent = '과거 이용 패턴으로 계산한 예상 방문량 기준입니다. 현재 체류인원이 아닙니다.';
  $('updated').textContent = ''; $('reference').textContent = ''; $('hours-note').textContent = '';
  $('sample').hidden = true;
}

function showError(message) {
  clear();
  $('now-when').textContent = '안내를 불러올 수 없습니다'; $('level').textContent = '—';
  $('actual').textContent = '자료 없음';
  $('error').textContent = message; $('error').hidden = false;
}

function describe(h) {
  const visitors = h.expected_visitors === null ? '자료 부족' : `약 ${h.expected_visitors}명`;
  const level = LABEL[h.level] ?? '판정 불가';
  const diff = h.difference_rate === null ? '비교 자료 없음' : `${h.difference_rate > 0 ? '+' : ''}${h.difference_rate}%`;
  const basis = `${METHOD[h.method] ?? h.method} · ${h.sample_count}건`;
  return { visitors, level, diff, basis };
}

function renderHours(hourly, isToday, nowHour) {
  const max = Math.max(1, ...hourly.map(h => h.expected_visitors ?? 0));
  hourly.forEach(h => {
    const d = describe(h);
    // 표 (접근성·검증용)
    const row = document.createElement('tr');
    [`${h.hour}시`, d.visitors, d.level, d.diff, d.basis].forEach(v => { const td = document.createElement('td'); td.textContent = v; row.append(td); });
    $('hourly').append(row);
    // 막대
    const col = document.createElement('button');
    col.type = 'button'; col.className = 'column' + (isToday && h.hour === nowHour ? ' current' : '');
    col.setAttribute('aria-label', `${h.hour}시 ${d.visitors} ${d.level}`);
    const bar = document.createElement('div');
    bar.className = 'bar ' + (h.level ?? 'none');
    bar.style.height = h.expected_visitors === null ? '3px' : `${Math.max(4, h.expected_visitors / max * 100)}%`;
    const lab = document.createElement('span'); lab.textContent = h.hour;
    col.append(bar, lab);
    col.addEventListener('click', () => {
      document.querySelectorAll('.column.selected').forEach(c => c.classList.remove('selected'));
      col.classList.add('selected');
      $('detail').textContent = `${h.hour}시 · ${d.visitors} · ${d.level} · 평소 대비 ${d.diff} · ${d.basis}`;
    });
    $('chart').append(col);
  });
}

async function load() {
  const current = ++generation;
  clear(); $('error').hidden = true;
  const day = $('date').value;
  try {
    const [meta, data] = await Promise.all([api.meta(), api.today(day)]);
    if (current !== generation) return;
    const now = kstNow();
    const isToday = day === kstDate();

    $('library').textContent = meta.library_name;
    $('sample').hidden = !data.is_sample;

    // 지금 / 선택한 날짜 요약
    $('now').dataset.level = data.congestion.level ?? '';
    $('now-when').textContent = isToday ? `오늘 ${koDate(day)} · ${now.getHours()}시 기준` : `${koDate(day)} · 시간대별 예측`;
    $('level').textContent = data.congestion.label;
    // 단계가 없을 때(다른 날짜 조회·자료 부족)는 큰 글자 대신 안내 문장 크기로
    $('level').classList.toggle('small', !data.congestion.level);
    if (data.basis) $('basis').textContent = data.basis + '. 과거 이용 패턴으로 계산한 예상값입니다.';

    // 추천
    const r = data.recommendation;
    $('best').textContent = r.best_start_hour === null ? '추천 자료 부족' : `${r.best_start_hour}시 – ${r.best_end_hour}시`;
    $('recommendation').textContent = r.message;

    renderHours(data.hourly, isToday, now.getHours());

    $('updated').textContent = '데이터 갱신 ' + fmtTime(data.updated_at);
    $('reference').textContent = '예측 기준 시각 ' + fmtTime(data.reference_time);
    $('hours-note').textContent = meta.hours_note ?? '';

    // 실제 집계 (있을 때만)
    try {
      const s = await api.stats(day);
      if (current !== generation) return;
      const status = s.data_status === 'partial' ? '현재까지 집계된 데이터 (부분 데이터)' : '완료된 수집 데이터';
      $('actual').textContent = `${status} · 일일 원본 IN ${s.total_in}명 / OUT ${s.total_out}명`;
      $('actual-note').textContent = `시간대 IN 합계 ${s.hourly_total_in}명. 원본 일일 합계와 다를 수 있으며 임의로 보정하지 않습니다.`;
    } catch (e) {
      if (current !== generation) return;
      $('actual').textContent = e.code === 'DATA_NOT_FOUND' ? '이 날짜의 수집 데이터가 아직 없습니다.' : e.message;
    }
  } catch (e) {
    if (current !== generation) return;
    showError(e.message);
  }
}

$('date').value = kstDate();
// 화면이 넓으면 표를 기본으로 펼침. 모바일은 접어 둠.
document.querySelector('details').open = window.matchMedia('(min-width: 600px)').matches;
$('date-form').addEventListener('submit', e => { e.preventDefault(); load(); });
$('refresh').addEventListener('click', load);
load();
