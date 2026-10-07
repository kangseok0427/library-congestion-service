"""Cloud HTTP contracts and real Excel -> JSON -> visitor integration."""
import hashlib
import json
from datetime import date, datetime, timezone
from urllib.parse import unquote
from uuid import uuid4
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.cloud_admin import CloudAdmin
from backend.neon_storage import Neon
from backend.cloud_versions import CloudProvider, CloudVersions
from backend.library_hours import KST
from library_etl.versions import VersionError
from tests.test_admin_api_v2 import make_excel


class MemoryStorage:
    """Network test double; transaction SQL itself is exercised with PostgreSQL."""
    mode='neon-direct'
    url='https://storage.neon.tech'
    upload_bucket='library-uploads'
    def __init__(self):
        self.tables={'library_sessions':[], 'library_uploads':[], 'library_versions':[],
                     'library_state':[{'active_version_id':None}]}
        self.objects={}
        self.fail_put=False
        self.fail_cleanup=False
        self.lease=None
        self.account={'id':str(uuid4()),'role':'admin'}

    def rows(self,table,query=''):
        rows=[dict(r) for r in self.tables[table]]
        for part in query.split('&'):
            if '=eq.' in part:
                key,value=part.split('=eq.',1)
                if key=='singleton': continue
                rows=[r for r in rows if str(r.get(key))==unquote(value)]
            if '=gt.' in part:
                key,value=part.split('=gt.',1)
                rows=[r for r in rows if r[key]>unquote(value)]
            if '=lt.' in part:
                key,value=part.split('=lt.',1)
                rows=[r for r in rows if r.get(key,'9999')<unquote(value)]
        if 'order=' in query:
            rows.sort(key=lambda r:r['id'],reverse=True)
        return rows

    def insert(self,table,row): self.tables[table].append(dict(row))
    def delete(self,table,query):
        matched=self.rows(table,query)
        self.tables[table]=[r for r in self.tables[table] if r not in matched]

    def login(self,email,password):
        if password!='correct': raise VersionError('bad','UNAUTHORIZED',401)
        return {'access_token':'synthetic-token','expires_in':3600,'user':self.account}
    def user(self,token): return self.account
    def logout(self,token): pass
    def sign_upload(self,path,size): return self.url+'/'+self.upload_bucket+'/'+path+'?signed=1'

    def download(self,path,limit=100*1024*1024):
        value=self.objects[path]
        if len(value)>limit: raise VersionError('large','FILE_TOO_LARGE',413)
        return value
    def put(self,path,content):
        if self.fail_put: raise VersionError('failed','PUBLISH_FAILED',503)
        self.objects[path]=content
    def remove(self,path):
        if self.fail_cleanup: raise VersionError('failed','PUBLISH_FAILED',503)
        self.objects.pop(path,None)

    def rpc(self,name,body=None):
        body=body or {}
        versions=self.tables['library_versions']
        if name=='library_list':
            active=self.tables['library_state'][0]['active_version_id']
            return {'active_version_id':active,'max_versions':4,'versions':[
                {k:r[k] for k in ('id','created_at','source_name','record_count')} | {'is_active':r['id']==active}
                for r in sorted(versions,key=lambda r:r['id'],reverse=True) if r['status']=='retained']}
        if name=='library_gc_candidates': return [dict(r) for r in versions if r['status']=='retired']
        if name=='library_begin':
            job=next(r for r in self.tables['library_uploads'] if r['id']==body['p_upload'])
            if job.get('result'): return job['result']
            if self.lease or any(r['status']!='retained' for r in versions):
                raise VersionError('busy','PUBLISH_IN_PROGRESS',409)
            self.lease=body['p_lease']
            versions.append({'id':body['p_version'],'object_path':body['p_path'],
                'source_name':body['p_source'],'status':'candidate','record_count':0,
                'created_at':datetime.now(KST).isoformat()})
        elif name=='library_commit':
            assert self.lease==body['p_lease']
            row=next(r for r in versions if r['id']==body['p_version'])
            row.update(status='retained',record_count=body['p_count'])
            self.tables['library_state'][0]['active_version_id']=row['id']
            retained=sorted([r for r in versions if r['status']=='retained'],key=lambda r:r['id'],reverse=True)
            for r in retained[4:]: r['status']='retired'
            next(r for r in self.tables['library_uploads'] if r['id']==body['p_upload'])['result']=body['p_result']
            self.lease=None
        elif name=='library_abort':
            if self.lease==body['p_lease']:
                for r in versions:
                    if r['status']=='candidate': r['status']='retired'
                self.lease=None
        elif name=='library_rollback':
            self.tables['library_state'][0]['active_version_id']=body['p_version']
        else: raise AssertionError(name)


@pytest.fixture
def cloud():
    storage=MemoryStorage()
    versions=CloudVersions(storage)
    app=create_app(CloudProvider(versions),clock=lambda:datetime(2026,9,10,12,tzinfo=KST),
                   admin_backend=CloudAdmin(storage,versions))
    return storage,versions,TestClient(app,base_url='https://testserver')


def login(client):
    response=client.post('/api/v1/admin/session',json={'username':'admin@example.com','password':'correct'})
    assert response.status_code==200
    assert 'HttpOnly' in response.headers['set-cookie'] and 'Secure' in response.headers['set-cookie']
    assert response.headers['cache-control']=='no-store'


def test_cloud_permissions_logout_and_roles(cloud):
    storage,versions,client=cloud
    assert client.get('/api/v1/admin/versions').status_code==401
    bad=client.post('/api/v1/admin/session',json={'username':'admin@example.com','password':'wrong'})
    assert bad.json()['error']['code']=='INVALID_CREDENTIALS'
    storage.account['role']='user'
    assert client.post('/api/v1/admin/session',json={'username':'admin@example.com','password':'correct'}).status_code==401
    storage.account['role']='admin'
    login(client)
    old_cookie=client.cookies.get('library_admin_session')
    assert client.delete('/api/v1/admin/session').content==b''
    client.cookies.set('library_admin_session',old_cookie)
    assert client.get('/api/v1/admin/versions').status_code==401


def test_direct_upload_publish_and_visitor_no_local_records(cloud,tmp_path):
    storage,versions,client=cloud
    login(client)
    assert client.get('/api/v1/admin/versions').json()['versions']==[]
    source=tmp_path/'records.xlsx'; make_excel(source)
    content=source.read_bytes()
    signed=client.post('/api/v1/admin/uploads/sign',json={'filename':source.name,'size':len(content)}).json()
    assert signed['upload_url'].startswith(storage.url+'/library-uploads/')
    assert 'synthetic-token' not in json.dumps(signed)
    job=storage.tables['library_uploads'][0]
    storage.objects[job['object_path']]=content
    body={'upload_id':signed['upload_id']}
    result=client.post('/api/v1/admin/uploads/publish',json=body)
    assert result.status_code==200,result.text
    assert result.json()['version']['record_count']==32
    assert client.get('/api/v1/congestion/today?date=2026-09-10').status_code==200
    assert client.get('/api/v1/health').json()=={'status':'ok'}
    assert client.post('/api/v1/admin/uploads/publish',json=body).json()==result.json()
    assert len(storage.tables['library_versions'])==1


def test_size_owner_and_csrf_checks(cloud):
    storage,versions,client=cloud
    login(client)
    assert client.post('/api/v1/admin/uploads/sign',json={'filename':'x.xlsx','size':10485761}).status_code==413
    assert client.post('/api/v1/admin/uploads/sign',json={'filename':'x.csv','size':1}).status_code==400
    assert client.post('/api/v1/admin/uploads/sign',json={'filename':'x.xlsx','size':1},headers={'Origin':'https://evil.example'}).status_code==403
    id=str(uuid4())
    storage.insert('library_uploads',{'id':id,'user_id':str(uuid4())})
    assert client.post('/api/v1/admin/uploads/publish',json={'upload_id':id}).status_code==400


def make_job(storage,content):
    id=str(uuid4());path='uploads/'+id+'.xlsx'
    job={'id':id,'user_id':storage.account['id'],'object_path':path,'source_name':'records.xlsx','byte_size':len(content)}
    storage.insert('library_uploads',job);storage.objects[path]=content
    return storage.tables['library_uploads'][-1]


def test_failed_publish_preserves_active_and_retention_rolls_back(cloud,tmp_path):
    storage,versions,client=cloud
    source=tmp_path/'records.xlsx';make_excel(source)
    first=versions.publish(make_job(storage,source.read_bytes()))['version']['id']
    provider=CloudProvider(versions); provider.get()
    with pytest.raises(VersionError,match='File|파일|zip'):
        versions.publish(make_job(storage,b'not-xlsx'))
    assert versions.state()['active_version_id']==first
    storage.fail_put=True
    with pytest.raises(VersionError): versions.publish(make_job(storage,source.read_bytes()))
    assert provider.get().records
    storage.fail_put=False
    for _ in range(5): versions.publish(make_job(storage,source.read_bytes()))
    listing=versions.list_versions()
    assert len(listing['versions'])==4
    assert len(storage.tables['library_versions'])==4
    older=listing['versions'][-1]['id']
    assert versions.rollback(older)['active_version_id']==older
    assert versions.state()['active_version_id']==older
    with pytest.raises(VersionError): versions.rollback(first)


def test_retired_cleanup_failure_blocks_fifth_candidate(cloud,tmp_path):
    storage,versions,_=cloud
    source=tmp_path/'records.xlsx';make_excel(source)
    for _ in range(4): versions.publish(make_job(storage,source.read_bytes()))
    storage.fail_cleanup=True
    versions.publish(make_job(storage,source.read_bytes()))
    assert len(storage.tables['library_versions'])==5
    with pytest.raises(VersionError): versions.publish(make_job(storage,source.read_bytes()))
    assert len(storage.tables['library_versions'])==5
