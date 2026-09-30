// T05 (ADE-9) 이용자 웹. 데이터는 api.js를 통해서만 받습니다.
const $ = id => document.getElementById(id);
const LABEL = { quiet: '여유', normal: '보통', busy: '혼잡' };
const METHOD = { same_weekday_hour: '같은 요일·시간 평균', same_weekday_same_hour: '같은 요일·시간 평균', same_hour_fallback: '같은 시간 평균 (요일 자료 부족)',
  observed_cumulative: '오늘 누적 IN − OUT', insufficient_samples: '표본 부족', unavailable: '자료 부족' };
// ADE-40 quality_status: 값이 없을 때 이유를 그대로 보여 줍니다. 0명이나 '여유'로 바꾸지 않습니다.
const QUALITY = { missing_out: 'OUT 결측', partial: '부분 수집', missing_gate: '출입구 누락', negative_balance: '누적 음수',
  insufficient_data: '자료 부족', insufficient_samples: '표본 부족' };
// 지표: ADE-40 응답(hourly에 estimated_present)이면 추정 체류 인원, 아니면 기존 예상 방문량.
// 기존 방문량을 체류 인원으로 이름만 바꿔 보여 주지 않기 위해 응답 필드로만 판단합니다.
const METRIC = {
  present: { key: 'estimated_present', name: '추정 체류 인원', basis: '과거 유효 자료의 추정 체류 인원(누적 IN − OUT) 기준입니다. 정확한 실시간 인원이나 좌석 점유율이 아닙니다.' },
  visitors: { key: 'expected_visitors', name: '예상 방문량', basis: '과거 이용 패턴으로 계산한 예상 방문량 기준입니다. 현재 체류인원이 아닙니다.' },
};
let shown = METRIC.visitors;   // 제목·안내가 오류 화면에서도 서로 어긋나지 않게
const metricOf = hourly => hourly.some(h => 'estimated_present' in h) ? METRIC.present : METRIC.visitors;
let generation = 0;

const kstDate = (value = new Date()) => {
  const parts = new Intl.DateTimeFormat('en', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(value));
  const part = type => parts.find(p => p.type === type).value;
  return `${part('year')}-${part('month')}-${part('day')}`;
};
const kstHour = value => Number(new Intl.DateTimeFormat('en', { timeZone: 'Asia/Seoul', hour: 'numeric', hourCycle: 'h23' }).format(new Date(value)));
const addDays = (day, days) => {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};
function setDateWindow(range) {
  $('date').min = range.min_date;
  $('date').max = range.max_date;
}
const fmtTime = iso => { const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }); };
const koDate = ymd => { const [y, m, d] = ymd.split('-'); const w = '일월화수목금토'[new Date(`${ymd}T00:00:00Z`).getUTCDay()]; return `${Number(m)}월 ${Number(d)}일 (${w})`; };

function clear() {
  $('hourly').replaceChildren(); $('chart').replaceChildren();
  $('now').dataset.level = ''; $('now-when').textContent = '불러오는 중'; $('level').textContent = '—'; $('level').classList.remove('small');
  $('best').textContent = '—'; $('recommendation').textContent = '';
  $('detail').textContent = '시간대를 선택하세요.';
  $('actual').textContent = '불러오는 중'; $('actual-note').textContent = '';
  $('basis').textContent = shown.basis;
  $('updated').textContent = ''; $('reference').textContent = ''; $('hours-note').textContent = '';
  $('sample').hidden = true;
}

function showError(message) {
  clear();
  $('now-when').textContent = '안내를 불러올 수 없습니다'; $('level').textContent = '—';
  $('actual').textContent = '자료 없음';
  $('error').textContent = message; $('error').hidden = false;
}

function setMetric(metric) {
  shown = metric;
  $('hours-title').textContent = `시간대별 ${metric.name}`;
  $('metric-col').textContent = metric.name;
  $('chart').setAttribute('aria-label', `시간대별 ${metric.name} 그래프`);
}

// 표·막대·상세·접근성 문구가 모두 이 결과 하나를 씁니다.
function describe(h, metric) {
  const value = h[metric.key] ?? null;
  const level = value === null ? null : h.level ?? null;   // 값이 없으면 단계 색도 쓰지 않음
  const missing = QUALITY[h.quality_status];
  const visitors = value === null ? (missing && missing !== '자료 부족' ? `추정 불가 (${missing})` : '자료 부족') : `약 ${value}명`;
  const label = LABEL[level] ?? (value === null ? '자료 부족' : '판정 불가');
  const diff = h.difference_rate == null ? '비교 자료 없음' : `${h.difference_rate > 0 ? '+' : ''}${h.difference_rate}%`;
  const method = h.calculation_basis ?? h.method;
  const basis = `${METHOD[method] ?? method} · ${h.sample_count}건`;
  return { value, level, visitors, label, diff, basis };
}

function renderHours(hourly, isToday, nowHour, metric) {
  const rows = hourly.map(h => describe(h, metric));
  const max = Math.max(1, ...rows.map(d => d.value ?? 0));
  hourly.forEach((h, i) => {
    const d = rows[i];
    const hour = h.start_hour ?? h.hour;
    const timeLabel = `${hour}~${h.end_hour ?? hour + 1}시`;
    // 표 (접근성·검증용)
    const row = document.createElement('tr');
    [timeLabel, d.visitors, d.label, d.diff, d.basis].forEach(v => { const td = document.createElement('td'); td.textContent = v; row.append(td); });
    $('hourly').append(row);
    // 막대
    const col = document.createElement('button');
    col.type = 'button'; col.className = 'column' + (isToday && hour === nowHour ? ' current' : '');
    col.setAttribute('aria-label', `${timeLabel} ${metric.name} ${d.visitors} ${d.label}`);
    const bar = document.createElement('div');
    bar.className = 'bar ' + (d.level ?? 'none');
    bar.style.height = d.value === null ? '3px' : `${Math.max(4, d.value / max * 100)}%`;
    const lab = document.createElement('span'); lab.textContent = hour;
    col.append(bar, lab);
    col.addEventListener('click', () => {
      document.querySelectorAll('.column.selected').forEach(c => c.classList.remove('selected'));
      col.classList.add('selected');
      $('detail').textContent = `${timeLabel} · ${metric.name} ${d.visitors} · ${d.label} · 평소 대비 ${d.diff} · ${d.basis}`;
    });
    $('chart').append(col);
  });
}

async function load() {
  const current = ++generation;
  clear(); $('error').hidden = true;
  const day = $('date').value;
  try {
    const meta = await api.meta();
    if (current !== generation) return;
    setDateWindow(meta.date_window);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || day < $('date').min || day > $('date').max || !$('date').checkValidity()) {
      showError('한국시간 기준 오늘부터 7일 후까지만 조회할 수 있습니다.');
      return;
    }
    const data = await api.today(day);
    if (current !== generation) return;
    // 다른 날짜의 응답을 선택한 날짜처럼 보여 주지 않습니다.
    if (data.date && data.date !== day) {
      showError('선택한 날짜의 예측을 받지 못했습니다. 잠시 후 다시 시도하세요.');
      return;
    }
    const metric = metricOf(data.hourly);
    const nowHour = kstHour(data.reference_time);
    const isToday = day === kstDate(data.reference_time);
    const closed = data.data_status === 'closed';

    $('library').textContent = meta.library_name;
    $('sample').hidden = !data.is_sample;

    // 지금 / 선택한 날짜 요약
    $('now').dataset.level = data.congestion.level ?? '';
    $('now-when').textContent = isToday ? `오늘 ${koDate(day)} · ${nowHour}시 기준` : `${koDate(day)} · 시간대별 예측`;
    $('level').textContent = data.congestion.label;
    // 단계가 없을 때(다른 날짜 조회·자료 부족)는 큰 글자 대신 안내 문장 크기로
    $('level').classList.toggle('small', !data.congestion.level);
    $('basis').textContent = metric.basis;
    if (data.basis) $('basis').textContent = data.basis + (metric === METRIC.present ? '. 정확한 실시간 인원이나 좌석 점유율이 아닙니다.' : '. 과거 이용 패턴으로 계산한 예상값입니다.');

    // 추천
    const r = data.recommendation;
    $('best').textContent = closed ? '휴관일' : !r.best_start_hour ? '추천 자료 부족' : `${r.best_start_hour}시 – ${r.best_end_hour}시`;
    $('recommendation').textContent = r.message;

    setMetric(metric);
    renderHours(data.hourly, isToday, nowHour, metric);
    if (closed) $('detail').textContent = '휴관일에는 시간대별 예측을 제공하지 않습니다.';

    $('updated').textContent = '데이터 갱신 ' + fmtTime(data.updated_at);
    $('reference').textContent = '예측 기준 시각 ' + fmtTime(data.reference_time);
    $('hours-note').textContent = meta.hours_note ?? '';

    // 실제 집계 (있을 때만)
    try {
      const s = await api.stats(day);
      if (current !== generation) return;
      if (s.data_status === 'closed' || s.data_status === 'pending') {
        $('actual').textContent = s.data_status === 'closed' ? '휴관일' : '집계 전';
        return;
      }
      const status = s.data_status === 'partial' ? '현재까지 집계된 데이터 (부분 데이터)' : '완료된 수집 데이터';
      $('actual').textContent = `${status} · 운영시간 IN ${s.hourly_total_in}명 / OUT ${s.hourly_total_out === null ? '집계 미제공' : s.hourly_total_out + '명'}`;
      $('actual-note').textContent = '운영시간 내 수집값만 합산합니다. 원본 일일 합계는 별도로 보존합니다.';
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
setDateWindow({ min_date: $('date').value, max_date: addDays($('date').value, 7) });
// 화면이 넓으면 표를 기본으로 펼침. 모바일은 접어 둠.
document.querySelector('details').open = window.matchMedia('(min-width: 600px)').matches;
$('date-form').addEventListener('submit', e => { e.preventDefault(); load(); });
$('refresh').addEventListener('click', load);
load();
