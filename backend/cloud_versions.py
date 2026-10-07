"""Immutable cloud JSON snapshots with a transactional, fenced active pointer."""
import json
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from threading import Lock
from urllib.parse import quote

from backend.domain import DataError, validate_records
from backend.library_hours import KST
from backend.service import LibraryService
from library_etl.pipeline import preprocess
from library_etl.refresh import merge_records
from library_etl.versions import VersionError


class CloudVersions:
    def __init__(self, storage):
        self.storage = storage

    def state(self):
        rows = self.storage.rows('library_state', 'singleton=eq.true')
        if len(rows) != 1:
            raise VersionError('클라우드 저장소 초기 설정이 필요합니다.', 'PUBLISH_FAILED', 503)
        return rows[0]

    def version(self, identifier):
        rows = self.storage.rows('library_versions', 'status=eq.retained&id=eq.'+quote(identifier, safe=''))
        if not rows:
            raise VersionError('보관 중인 버전을 찾을 수 없습니다.', 'VERSION_NOT_FOUND', 404)
        return rows[0]

    def records(self, identifier=None):
        identifier = identifier or self.state()['active_version_id']
        if identifier is None:
            return [], None
        row = self.version(identifier)
        try:
            records = validate_records(json.loads(self.storage.download(row['object_path']).decode('utf-8')))
            if not records:
                raise ValueError()
        except (ValueError, UnicodeError) as exc:
            raise VersionError('정상 데이터를 읽지 못했습니다.', 'PUBLISH_FAILED', 503) from exc
        return records, row

    def public_version(self, row, active):
        return {key: row[key] for key in ('id','created_at','source_name','record_count')} | {'is_active':row['id']==active}

    def list_versions(self):
        return self.storage.rpc('library_list')

    def cleanup(self):
        for row in self.storage.rpc('library_gc_candidates'):
            self.storage.remove(row['object_path'])
            self.storage.delete('library_versions','status=eq.retired&id=eq.'+quote(row['id'],safe=''))

    def publish(self, job):
        if job.get('result'):
            return job['result']
        self.cleanup()  # A failed cleanup blocks another candidate beyond five.
        now = datetime.now(KST)
        known = {row['id'] for row in self.storage.rows('library_versions')}
        while now.strftime('%Y%m%dT%H%M%S%z') in known:
            now += timedelta(seconds=1)
        identifier = now.strftime('%Y%m%dT%H%M%S%z')
        lease = str(uuid4())
        object_path = 'versions/'+str(uuid4())+'.json'
        previous_result = self.storage.rpc('library_begin', {'p_lease':lease,'p_version':identifier,
                                         'p_path':object_path,'p_source':job['source_name'],'p_upload':job['id']})
        if previous_result is not None:
            return previous_result
        # Once begun, failures leave a durable candidate. It cannot become active
        # without the same unexpired lease; later cleanup safely retires it.
        try:
            content = self.storage.download(job['object_path'], 10*1024*1024)
            if len(content) != job['byte_size']:
                raise VersionError('업로드 파일 크기가 일치하지 않습니다.', 'INVALID_EXCEL', 422)
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory)/'upload.xlsx'
                source.write_bytes(content)
                try:
                    with zipfile.ZipFile(source) as archive:
                        if sum(item.file_size for item in archive.infolist()) > 256*1024*1024:
                            raise DataError('Excel 압축 해제 크기가 너무 큽니다.')
                    incoming, report = preprocess(source, source_name=job['source_name'])
                    existing, _ = self.records()
                    merged = merge_records(existing,incoming)
                    LibraryService(merged)  # Validate the same service boundary.
                except (DataError, zipfile.BadZipFile) as exc:
                    details = [{**{k:v for k,v in issue.items() if k!='message'},
                                'reason':issue.get('message',str(exc))}
                               for issue in getattr(exc,'issues',[])]
                    raise VersionError(str(exc),'INVALID_EXCEL',422,details) from exc
                payload = json.dumps(merged,ensure_ascii=False,allow_nan=False).encode()
                if len(payload)>45_000_000:
                    raise VersionError('처리 결과가 저장 한도를 초과했습니다.','FILE_TOO_LARGE',413)
                self.storage.put(object_path,payload)
            warnings = [{'code':w['code'], 'message':w.get('message','원본 합계와 시간대 합계가 다릅니다.'),
                         **({'row':w['row']} if 'row' in w else {})} for w in report['warnings'][:100]]
            result = {'status':'published','version':{'id':identifier,'created_at':now.isoformat(),
                      'source_name':job['source_name'],'record_count':len(merged),'is_active':True},
                      'validation':{'warning_count':len(report['warnings']),'warnings':warnings}}
            self.storage.rpc('library_commit', {'p_lease':lease,'p_version':identifier,
                'p_count':len(merged),'p_upload':job['id'],'p_result':result})
        except Exception as exc:
            # Release only our lease. A lost/ambiguous commit must not be undone.
            try:
                self.storage.rpc('library_abort',{'p_lease':lease})
            except VersionError:
                pass
            if isinstance(exc, VersionError):
                raise
            raise VersionError('파일 처리에 실패하여 기존 정상본을 유지했습니다.', 'PUBLISH_FAILED', 500) from exc
        try:
            self.cleanup()
            self.storage.remove(job['object_path'])
        except VersionError:
            pass  # Publication already committed; cleanup retries on next upload.
        return result

    def rollback(self, identifier):
        self.records(identifier)  # Validate before changing the active pointer.
        self.storage.rpc('library_rollback',{'p_version':identifier})
        return {'status':'rolled_back','active_version_id':identifier}


class CloudProvider:
    def __init__(self, versions):
        self.versions = versions
        self.lock = Lock()
        self.cached_id, self.service = None, None

    def get(self):
        # Never cache the mutable active pointer across serverless invocations.
        for _ in range(2):
            identifier = self.versions.state()['active_version_id']
            try:
                if identifier is None:
                    raise VersionError('관리자가 첫 Excel 파일을 업로드해야 합니다.', 'DATA_NOT_FOUND', 404)
                with self.lock:
                    if self.cached_id != identifier:
                        records,row = self.versions.records(identifier)
                        self.service = LibraryService(records, row['created_at'], sample=False)
                        self.cached_id = identifier
                    return self.service
            except VersionError as exc:
                if exc.code!='VERSION_NOT_FOUND':
                    raise
        raise VersionError('데이터 게시 중입니다. 다시 조회하세요.', 'PUBLISH_IN_PROGRESS', 409)
