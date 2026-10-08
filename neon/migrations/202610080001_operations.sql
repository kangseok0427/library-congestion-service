-- Additive migration. Excel snapshots and publication state are unchanged.
create table if not exists library.library_closures (
  closed_date date primary key,
  reason text not null check (length(reason) between 1 and 80)
);
insert into library.library_closures values
  ('2026-09-24','추석 연휴'), ('2026-09-25','추석 연휴'), ('2026-09-26','추석 연휴')
on conflict do nothing;
create table if not exists library.library_visitors (
  visitor_hash text primary key check (visitor_hash ~ '^[a-f0-9]{64}$'),
  first_seen date not null
);
create table if not exists library.library_visit_days (
  visit_date date not null,
  visitor_hash text not null references library.library_visitors(visitor_hash),
  page_views bigint not null check (page_views > 0),
  primary key (visit_date, visitor_hash)
);
alter table library.library_closures enable row level security;
alter table library.library_visitors enable row level security;
alter table library.library_visit_days enable row level security;
revoke all on library.library_closures, library.library_visitors, library.library_visit_days from public;

create or replace function library.library_closure_list()
returns jsonb language sql stable security definer set search_path=library, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('date',closed_date,'reason',reason) order by closed_date),'[]'::jsonb)
  from library_closures
$$;
create or replace function library.library_closure_set(p_date date, p_reason text)
returns void language sql security definer set search_path=library, pg_temp as $$
  insert into library_closures values(p_date,p_reason)
  on conflict(closed_date) do update set reason=excluded.reason
$$;
create or replace function library.library_closure_remove(p_date date)
returns void language sql security definer set search_path=library, pg_temp as $$
  delete from library_closures where closed_date=p_date
$$;
create or replace function library.library_visit(p_visitor text, p_date date)
returns void language plpgsql security definer set search_path=library, pg_temp as $$
begin
  insert into library_visitors values(p_visitor,p_date) on conflict do nothing;
  insert into library_visit_days values(p_date,p_visitor,1)
  on conflict(visit_date,visitor_hash) do update set page_views=library_visit_days.page_views+1;
end $$;
create or replace function library.library_traffic(p_date date)
returns jsonb language sql stable security definer set search_path=library, pg_temp as $$
  select jsonb_build_object('date',p_date,
    'today_visitors',(select count(*) from library_visit_days where visit_date=p_date),
    'total_visitors',(select count(*) from library_visitors),
    'today_page_views',(select coalesce(sum(page_views),0) from library_visit_days where visit_date=p_date),
    'total_page_views',(select coalesce(sum(page_views),0) from library_visit_days))
$$;
revoke all on function library.library_closure_list(), library.library_closure_set(date,text),
  library.library_closure_remove(date), library.library_visit(text,date), library.library_traffic(date) from public;
grant execute on function library.library_closure_list(), library.library_closure_set(date,text),
  library.library_closure_remove(date), library.library_visit(text,date), library.library_traffic(date) to library_backend;
