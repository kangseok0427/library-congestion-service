// T05 이용자 웹. ADE-49에서 막대와 방문 안내 멘트만 남기도록 단순화했습니다.
// 데이터는 api.js를 통해서만 받고, 혼잡 단계는 서버가 준 level/label을 그대로 씁니다.
const $ = id => document.getElementById(id);
const LABEL = { quiet: '여유', normal: '보통', busy: '혼잡' };
// 값이 없을 때 이유. 0명이나 '여유'로 바꾸지 않습니다(ADE-40/41).
const QUALITY = { missing_out: 'OUT 결측', partial: '부분 수집', missing_gate: '출입구 누락', negative_balance: '누적 음수',
  insufficient_data: '자료 부족', insufficient_samples: '표본 부족' };
// 막대 높이 지표. estimated_present가 없는 예전 응답은 예상 방문량으로 그리되 이름을 바꿔 붙이지 않습니다.
const METRIC = {
  present: { key: 'estimated_present', basis: '막대 높이는 과거 자료로 추정한 체류 인원입니다. 정확한 실시간 인원이나 좌석 점유율이 아닙니다.' },
  visitors: { key: 'expected_visitors', basis: '막대 높이는 과거 이용 패턴으로 계산한 예상 방문량입니다. 현재 체류 인원이 아닙니다.' },
};
const metricOf = hourly => hourly.some(h => 'estimated_present' in h) || !hourly.some(h => 'expected_visitors' in h) ? METRIC.present : METRIC.visitors;
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
const koDate = ymd => { const [, m, d] = ymd.split('-'); const w = '일월화수목금토'[new Date(`${ymd}T00:00:00Z`).getUTCDay()]; return `${Number(m)}월 ${Number(d)}일 (${w})`; };

function setGuide(state, when, title, message) {
  $('guide').hidden = false;
  $('guide').dataset.state = state;
  $('guide-when').textContent = when;
  $('guide-title').textContent = title;
  $('guide-title').hidden = !title;
  $('guide-message').textContent = message;
}

function clear() {
  $('chart').replaceChildren();
  $('detail').textContent = '막대를 누르면 그 시간대 상태가 보입니다.';
  $('hours').hidden = false;
  setGuide('loading', '불러오는 중', '', '');
  $('updated').textContent = ''; $('hours-note').textContent = '';
  $('sample').hidden = true;
}

function showError(message) {
  clear();
  // 이전 날짜의 막대·안내가 남아 있지 않게 둘 다 숨깁니다.
  $('guide').hidden = true;
  $('hours').hidden = true;
  $('error').textContent = message; $('error').hidden = false;
}

// 막대·상세·접근성 문구가 모두 이 결과 하나를 씁니다. 숫자는 화면에 쓰지 않습니다.
function describe(h, metric) {
  const value = h[metric.key] ?? null;
  const level = value === null ? null : h.level ?? null;   // 값이 없으면 단계 색도 쓰지 않음
  let text;
  if (value === null) {
    const reason = QUALITY[h.quality_status];
    text = reason && reason !== '자료 부족' ? `자료 부족 (${reason})` : '자료 부족';
  } else {
    text = level ? (h.label || LABEL[level]) : '판정 불가';
  }
  return { value, level, text };
}

function renderBars(hourly, metric, currentHour) {
  const rows = hourly.map(h => describe(h, metric));
  const max = Math.max(1, ...rows.map(d => d.value ?? 0));
  hourly.forEach((h, i) => {
    const d = rows[i];
    const hour = h.start_hour ?? h.hour;
    const time = `${hour}~${h.end_hour ?? hour + 1}시`;
    const current = hour === currentHour;
    const col = document.createElement('button');
    col.type = 'button';
    col.className = 'column' + (current ? ' current' : '');
    col.dataset.hour = hour;
    col.dataset.level = d.level ?? 'none';
    col.dataset.present = d.value ?? '';
    col.setAttribute('aria-label', `${time} ${d.text}${current ? ', 지금' : ''}`);
    const bar = document.createElement('div');
    bar.className = 'bar ' + (d.level ?? 'none');
    // 자료 부족은 0으로 그리지 않고 같은 높이의 회색 짧은 막대로 구분합니다.
    bar.style.height = d.value === null ? '8px' : `${Math.max(4, d.value / max * 100)}%`;
    const lab = document.createElement('span'); lab.textContent = hour;
    col.append(bar, lab);
    col.addEventListener('click', () => {
      document.querySelectorAll('.column.selected').forEach(c => c.classList.remove('selected'));
      col.classList.add('selected');
      $('detail').textContent = `${time} ${d.text}`;
    });
    $('chart').append(col);
  });
  return rows;
}

async function load() {
  const current = ++generation;
  clear(); $('error').hidden = true;
  let day = $('date').value;
  try {
    const meta = await api.meta();
    if (current !== generation) return;
    setDateWindow(meta.date_window);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || day < $('date').min || day > $('date').max || !$('date').checkValidity()) {
      showError('한국시간 기준 오늘부터 7일 후까지만 조회할 수 있습니다.');
      return;
    }
    const availability = meta.date_availability;
    const skipped = availability?.skipped_dates || [];
    if (skipped.includes(day)) {
      const available = (availability.available_dates || []).filter(d => d >= $('date').min && d <= $('date').max).sort();
      const next = available.find(d => d > day) || available[available.length - 1];
      if (!next) {
        setGuide('insufficient', koDate(day), '자료 없음', '조회 기간에 같은 날짜의 과거 기록이 없습니다.');
        $('hours').hidden = true;
        return;
      }
      $('date-hint').textContent = `${koDate(day)}의 과거 기록이 없어 ${koDate(next)}로 건너뛰었습니다.`;
      day = next;
      $('date').value = day;
    } else {
      $('date-hint').textContent = '오늘부터 7일 후까지 볼 수 있습니다. 과거 기록이 없는 날짜는 건너뜁니다.';
    }
    const data = await api.today(day);
    if (current !== generation) return;
    // 다른 날짜의 응답을 선택한 날짜처럼 보여 주지 않습니다.
    if (data.date && data.date !== day) {
      showError('선택한 날짜의 통계를 받지 못했습니다. 잠시 후 다시 시도하세요.');
      return;
    }
    const reference = data.reference_time || new Date().toISOString();
    const isToday = day === kstDate(reference);
    const when = `${isToday ? '오늘 ' : ''}${koDate(day)}`;
    const message = (data.recommendation && data.recommendation.message) || '';

    $('library').textContent = meta.library_name;
    $('sample').hidden = !data.is_sample;
    $('updated').textContent = data.updated_at ? '데이터 갱신 ' + fmtTime(data.updated_at) : '';
    $('hours-note').textContent = meta.hours_note ?? '';

    if (data.data_status === 'closed') {
      setGuide('closed', when, '휴관일', message);
      $('hours').hidden = true;
      return;
    }

    const metric = metricOf(data.hourly);
    if (data.data_status === 'historical_statistics') {
      const years = data.statistics?.source_years || [];
      const matched = data.statistics?.matched_dates;
      const sources = matched?.length ? `같은 월·일의 과거 기록 ${matched.length}일 중 계산 가능한 시간대만 표시합니다.`
        : years.length ? `${years.join('·')}년의 같은 월·일 기록을 사용했습니다.`
        : data.hourly.some(h => h[metric.key] != null) ? '사용한 연도 정보가 제공되지 않았습니다.' : '과거 같은 월·일의 유효 기록이 없습니다.';
      $('basis').textContent = `막대 높이는 과거 같은 날짜의 추정 체류 인원 평균 통계입니다. ${sources} 실시간 인원이나 미래 인원 예측이 아닙니다.`;
    } else {
      $('basis').textContent = metric.basis;
    }
    const rows = renderBars(data.hourly, metric, isToday ? kstHour(reference) : null);
    if (!rows.some(d => d.value !== null)) {
      setGuide('insufficient', when, '자료 부족', message || '이 날짜와 같은 월·일의 과거 기록이 부족합니다.');
    } else {
      setGuide('open', when, '', message);
    }
  } catch (e) {
    if (current !== generation) return;
    showError(e.message);
  }
}

$('date').value = kstDate();
setDateWindow({ min_date: $('date').value, max_date: addDays($('date').value, 7) });
$('date-form').addEventListener('submit', e => { e.preventDefault(); load(); });
$('refresh').addEventListener('click', load);
load();
