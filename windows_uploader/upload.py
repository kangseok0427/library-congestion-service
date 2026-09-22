"""Provisional HTTP transport. T12 must define the production request contract."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class UploadError(RuntimeError):
    pass


def validate_endpoint(endpoint):
    try:
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment or any(c.isspace() for c in endpoint)):
            raise ValueError()
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise UploadError("유효한 HTTPS 업로드 주소를 입력하세요.") from exc
    return endpoint


class UploadClient:
    def __init__(self, endpoint, *, transport=urlopen, sleeper=time.sleep, retries=2, timeout=20,
                 auth_headers=None, method="POST", encode=None):
        self.endpoint = validate_endpoint(endpoint)
        self.transport = transport
        self.sleeper = sleeper
        self.retries = retries
        self.timeout = timeout
        self.auth_headers = auth_headers or (lambda token: {"Authorization": "Bearer " + token})
        self.method = method
        self.encode = encode or (lambda records: json.dumps(records, ensure_ascii=False,
                                                            allow_nan=False).encode("utf-8"))

    def send(self, records, token):
        if not token:
            raise UploadError("저장된 인증 토큰이 없습니다.")
        # The records-array body and POST method are provisional and configurable
        # only for mock/integration work until T12 publishes its contract.
        body = self.encode(records)
        for attempt in range(self.retries + 1):
            request = Request(self.endpoint, data=body, method=self.method, headers={
                "Content-Type": "application/json", **self.auth_headers(token),
            })
            try:
                with self.transport(request, timeout=self.timeout) as response:
                    status = response.status
                    response.read()
            except HTTPError as exc:
                status = exc.code
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < self.retries:
                    self.sleeper(min(2 ** attempt, 4))
                    continue
                raise UploadError("네트워크 연결 또는 응답 시간 초과로 업로드에 실패했습니다.") from None
            if 200 <= status < 300:
                return status
            if 500 <= status < 600 and attempt < self.retries:
                self.sleeper(min(2 ** attempt, 4))
                continue
            raise UploadError(f"업로드가 거부되었습니다. HTTP {status}")
        raise UploadError("업로드 재시도 횟수를 초과했습니다.")
