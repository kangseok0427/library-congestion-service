const $ = id => document.getElementById(id);
const labels = {quiet:'여유',normal:'보통',busy:'혼잡'};
const methods = {same_weekday_hour:'동일 요일·시간 평균',same_hour_fallback:'같은 시간 평균 (자료 부족)',unavailable:'자료 부족'};
let generation = 0;
function kstDate() { return new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date()); }
async function api(url) { const response=await fetch(url,{cache:'no-store'}); const data=await response.json(); if(!response.ok) throw new Error(data.error?.message || '데이터를 불러올 수 없습니다.'); return data; }
function clear() { $('hourly').replaceChildren(); $('chart').replaceChildren(); $('level').textContent='불러오는 중'; $('best').textContent='—'; $('recommendation').textContent=''; $('actual').textContent='불러오는 중'; $('actual-note').textContent=''; $('updated').textContent=''; $('reference').textContent=''; $('sample').hidden=true; }
async function load() {
 const current=++generation; clear(); $('error').hidden=true;
 try {
  const day=encodeURIComponent($('date').value);
  const [meta,data]=await Promise.all([api('/api/v1/meta'),api('/api/v1/congestion/today?date='+day)]);
  if(current!==generation)return;
  $('sample').hidden=!data.is_sample; $('level').textContent=data.congestion.label;
  const r=data.recommendation; $('best').textContent=r.best_start_hour===null?'추천 자료 부족':`${r.best_start_hour}시–${r.best_end_hour}시`;
  $('recommendation').textContent=r.message;
  const max=Math.max(1,...data.hourly.map(h=>h.expected_visitors??0));
  data.hourly.forEach(h=>{
   const row=document.createElement('tr');
   const values=[`${h.hour}시`,h.expected_visitors===null?'자료 부족':`${h.expected_visitors}명`,labels[h.level]??'자료 부족',h.difference_rate===null?'비교 자료 없음':`${h.difference_rate}%`,`${methods[h.method]} · ${h.sample_count}건`];
   values.forEach(value=>{const td=document.createElement('td');td.textContent=value;row.append(td);}); $('hourly').append(row);
   const column=document.createElement('div');column.className='column';const bar=document.createElement('div');bar.className='bar '+(h.level??'');bar.style.height=`${h.expected_visitors===null?0:Math.max(2,h.expected_visitors/max*145)}px`;bar.title=`${h.hour}시: ${values[1]}`;const label=document.createElement('span');label.textContent=h.hour;column.append(bar,label);$('chart').append(column);
  });
  $('updated').textContent='데이터 갱신: '+data.updated_at; $('reference').textContent='예측 기준 시각: '+data.reference_time; $('hours-note').textContent=meta.hours_note;
  try {const stats=await api('/api/v1/stats?date='+day);if(current!==generation)return;$('actual').textContent=`${stats.data_status==='partial'?'현재까지 집계된 데이터 (부분 데이터)':'완료된 수집 데이터'} · 일일 원본 IN ${stats.total_in}명 / OUT ${stats.total_out}명`;$('actual-note').textContent=`시간대 IN 합계 ${stats.hourly_total_in}명. 일일 원본 합계와 다를 수 있으며 임의 보정하지 않습니다.`;}
  catch(e){if(current===generation)$('actual').textContent=e.message;}
 } catch(e){if(current!==generation)return;clear();$('level').textContent='안내를 불러올 수 없습니다';$('actual').textContent='자료 없음';$('error').textContent=e.message;$('error').hidden=false;}
}
$('date').value=kstDate();$('date-form').addEventListener('submit',e=>{e.preventDefault();load();});$('refresh').addEventListener('click',load);load();
