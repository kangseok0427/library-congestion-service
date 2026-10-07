// Actual PostgreSQL execution, not a mock of the transaction functions.
import {PGlite} from '@electric-sql/pglite';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';

const db = new PGlite();
await db.exec(`create role anon; create role authenticated; create role service_role;
  create schema storage; create table storage.buckets(
    id text primary key,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]);`);
await db.exec(await readFile(new URL('../supabase/migrations/202610070001_cloud.sql',import.meta.url),'utf8'));
const query = (sql,args=[])=>db.query(sql,args);
const active = async()=> (await query('select * from library_state')).rows[0];
const begin = (lease,id,upload)=>query('select library_begin($1,$2,$3,$4,$5)',[lease,id,'versions/'+id+'.json','sample.xlsx',upload]);
const commit = (lease,id,upload)=>query('select library_commit($1,$2,32,$3,$4)',[lease,id,upload,JSON.stringify({status:'published',version:{id}})]);
const job = async()=>{
  const id=randomUUID();
  await query('insert into library_uploads(id,user_id,object_path,source_name,byte_size) values($1,$2,$3,$4,1)',[id,randomUUID(),'uploads/'+id+'.xlsx','sample.xlsx']);
  return id;
};
const firstJob=await job();
const lease=randomUUID();
await begin(lease,'v1',firstJob);
await assert.rejects(begin(randomUUID(),'racing',await job()),/PUBLISH_IN_PROGRESS/);
await assert.rejects(commit(randomUUID(),'v1',firstJob),/PUBLISH_IN_PROGRESS/);
assert.equal((await active()).active_version_id,null);
await commit(lease,'v1',firstJob);
assert.equal((await active()).active_version_id,'v1');
const listed=(await query('select library_list()')).rows[0].library_list;
assert.equal(listed.versions[0].is_active,true);
assert.equal(listed.active_version_id,'v1');
const replay = await begin(randomUUID(),'unused',firstJob);
assert.equal(replay.rows[0].library_begin.version.id,'v1');
assert.equal((await query('select count(*) from library_versions')).rows[0].count,1);

for (let n=2;n<=5;n++) {
  const upload=await job(), lease=randomUUID();
  await begin(lease,'v'+n,upload);
  await commit(lease,'v'+n,upload);
}
assert.equal((await query("select count(*) from library_versions where status='retained'")).rows[0].count,4);
assert.equal((await query('select count(*) from library_versions')).rows[0].count,5);
await assert.rejects(begin(randomUUID(),'sixth',await job()),/PUBLISH_IN_PROGRESS/);
await assert.rejects(query("select library_rollback('v1')"),/VERSION_NOT_FOUND/);
await query("select library_rollback('v2')");
assert.equal((await active()).active_version_id,'v2');
const garbage=(await query('select * from library_gc_candidates()')).rows;
assert.equal(garbage.length,1);
await query("delete from library_versions where status='retired'");

const expiredLease=randomUUID(), expiredJob=await job();
await begin(expiredLease,'expired',expiredJob);
await query("update library_state set lease_until=now()-interval '1 second'");
await assert.rejects(commit(expiredLease,'expired',expiredJob),/PUBLISH_IN_PROGRESS/);
await query('select library_gc_candidates()');
assert.equal((await active()).active_version_id,'v2');
assert.equal((await query("select status from library_versions where id='expired'")).rows[0].status,'retired');
await query("delete from library_versions where status='retired'");
const freshLease=randomUUID();
await begin(freshLease,'fresh',await job());
await query('select library_abort($1)',[expiredLease]);
assert.equal((await active()).lease_id,freshLease);
await query('select library_abort($1)',[freshLease]);
assert.equal((await active()).active_version_id,'v2');
await db.exec('set role anon');
await assert.rejects(query('select * from library_state'),/permission denied/);
await assert.rejects(query("select library_rollback('v2')"),/permission denied/);
await db.exec('reset role');
await db.close();
console.log('Cloud SQL PASS: publication, fencing, idempotency, retention, rollback, recovery, permissions');
