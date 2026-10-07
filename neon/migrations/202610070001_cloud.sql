-- Run once as neondb_owner after creating an ordinary restricted library_backend login.
create schema library;
-- JSON remains in private Storage. Postgres controls publication atomically.
create table library.library_state (
  singleton boolean primary key default true check(singleton),
  active_version_id text,
  lease_id uuid,
  lease_until timestamptz
);
insert into library.library_state(singleton) values(true);
create table library.library_versions (
  id text primary key,
  object_path text unique not null,
  created_at timestamptz not null default now(),
  source_name text not null,
  record_count integer not null default 0,
  status text not null check(status in ('candidate','retained','retired'))
);
create table library.library_uploads (
  id uuid primary key,
  user_id text not null,
  object_path text unique not null,
  source_name text not null,
  byte_size integer not null check(byte_size between 1 and 10485760),
  created_at timestamptz not null default now(),
  result jsonb
);
create table library.library_sessions (
  token_hash text primary key,
  user_id text not null,
  expires_at timestamptz not null
);
alter table library.library_state enable row level security;
alter table library.library_versions enable row level security;
alter table library.library_uploads enable row level security;
alter table library.library_sessions enable row level security;
revoke all on schema library from public;
grant usage on schema library to library_backend;
grant select on library.library_state, library.library_versions to library_backend;
grant delete on library.library_versions to library_backend;
grant select, insert, delete on library.library_uploads, library.library_sessions to library_backend;
create policy backend_read_state on library.library_state for select to library_backend using (true);
create policy backend_read_versions on library.library_versions for select to library_backend using (true);
create policy backend_delete_retired on library.library_versions for delete to library_backend using (status='retired');
create policy backend_uploads on library.library_uploads to library_backend using (true) with check (true);
create policy backend_sessions on library.library_sessions to library_backend using (true) with check (true);
create index library_uploads_created on library.library_uploads(created_at);
create index library_sessions_expiry on library.library_sessions(expires_at);

create function library.library_begin(p_lease uuid, p_version text, p_path text, p_source text, p_upload uuid)
returns jsonb language plpgsql security definer set search_path=library, pg_temp as $$
declare s library.library_state; previous_result jsonb;
begin
  select * into s from library_state where singleton for update;
  select result into previous_result from library_uploads where id=p_upload;
  if not found then raise exception 'PUBLISH_FAILED'; end if;
  if previous_result is not null then return previous_result; end if;
  if s.lease_until > now() or exists(select 1 from library_versions where status <> 'retained') then
    raise exception 'PUBLISH_IN_PROGRESS';
  end if;
  insert into library_versions(id,object_path,source_name,status) values(p_version,p_path,p_source,'candidate');
  update library_state set lease_id=p_lease, lease_until=now()+interval '10 minutes' where singleton;
  return null;
end $$;

create function library.library_commit(p_lease uuid, p_version text, p_count integer, p_upload uuid, p_result jsonb)
returns void language plpgsql security definer set search_path=library, pg_temp as $$
declare s library.library_state;
begin
  select * into s from library_state where singleton for update;
  if s.lease_id is distinct from p_lease or s.lease_until <= now() then raise exception 'PUBLISH_IN_PROGRESS'; end if;
  update library_versions set status='retained',record_count=p_count where id=p_version and status='candidate';
  if not found then raise exception 'PUBLISH_FAILED'; end if;
  update library_state set active_version_id=p_version,lease_id=null,lease_until=null where singleton;
  update library_versions set status='retired' where status='retained' and id not in (
    select id from library_versions where status='retained' order by created_at desc,id desc limit 4
  );
  update library_uploads set result=p_result where id=p_upload;
end $$;

create function library.library_rollback(p_version text)
returns void language plpgsql security definer set search_path=library, pg_temp as $$
declare s library.library_state;
begin
  select * into s from library_state where singleton for update;
  if s.lease_until > now() then raise exception 'PUBLISH_IN_PROGRESS'; end if;
  if not exists(select 1 from library_versions where id=p_version and status='retained') then raise exception 'VERSION_NOT_FOUND'; end if;
  update library_state set active_version_id=p_version where singleton;
end $$;

create function library.library_gc_candidates()
returns setof library.library_versions language plpgsql security definer set search_path=library, pg_temp as $$
declare s library.library_state;
begin
  select * into s from library_state where singleton for update;
  if s.lease_until is null or s.lease_until <= now() then
    update library_versions set status='retired' where status='candidate';
    update library_state set lease_id=null,lease_until=null where singleton;
  end if;
  return query select * from library_versions where status='retired';
end $$;

create function library.library_abort(p_lease uuid)
returns void language plpgsql security definer set search_path=library, pg_temp as $$
begin
  perform 1 from library_state where singleton for update;
  if exists(select 1 from library_state where singleton and lease_id=p_lease) then
    update library_versions set status='retired' where status='candidate';
    update library_state set lease_id=null,lease_until=null where singleton;
  end if;
end $$;
create function library.library_list()
returns jsonb language sql stable security definer set search_path=library, pg_temp as $$
  select jsonb_build_object('active_version_id',s.active_version_id,'max_versions',4,
    'versions',coalesce((select jsonb_agg(jsonb_build_object(
      'id',v.id,'created_at',v.created_at,'source_name',v.source_name,
      'record_count',v.record_count,'is_active',v.id=s.active_version_id)
      order by v.created_at desc,v.id desc) from library_versions v where v.status='retained'),'[]'::jsonb))
    from library_state s where s.singleton
$$;
revoke all on function library.library_begin(uuid,text,text,text,uuid), library.library_commit(uuid,text,integer,uuid,jsonb), library.library_rollback(text), library.library_gc_candidates(), library.library_abort(uuid) from public;
grant execute on function library.library_begin(uuid,text,text,text,uuid), library.library_commit(uuid,text,integer,uuid,jsonb), library.library_rollback(text), library.library_gc_candidates(), library.library_abort(uuid) to library_backend;
revoke all on function library.library_list() from public;
grant execute on function library.library_list() to library_backend;

