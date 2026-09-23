"""Provisional HTTP transport. T12 must define the production request contract."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class UploadError(RuntimeError):
    pass


# ADE-35 contract: POST records array with Authorization: Bearer token.
PRODUCTION_UPLOAD_ENDPOINT = "https://ade0033.pythonanywhere.com/api/v1/admin/records"
APPROVED_PRODUCTION_HOSTS = frozenset({"ade0033.pythonanywhere.com"})
DEFAULT_TIMEOUT_SECONDS = 20 * 60


def validate_endpoint(endpoint):
    try:
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or "?" in endpoint or "#" in endpoint
                or any(c.isspace() for c in endpoint)):
            raise ValueError()
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise UploadError("유효한 HTTPS 업로드 주소를 입력하세요.") from exc
    return endpoint


class UploadClient:
    def __init__(self, endpoint, *, mode="production", approved_endpoint=PRODUCTION_UPLOAD_ENDPOINT,
                 approved_hosts=APPROVED_PRODUCTION_HOSTS, transport=None, sleeper=time.sleep,
                 retries=2, timeout=DEFAULT_TIMEOUT_SECONDS, auth_headers=None, method="POST", encode=None):
        self.endpoint = validate_endpoint(endpoint)
        if mode == "production":
            approved = validate_endpoint(approved_endpoint) if approved_endpoint else None
            if (not approved or self.endpoint != approved
                    or urlsplit(self.endpoint).hostname not in approved_hosts):
                raise UploadError("T12 API 계약 확정 후 승인된 업로드 주소 연결이 필요합니다.")
            self.transport = transport or urlopen
        elif mode == "mock" and transport is not None and transport is not urlopen:
            self.transport = transport
        else:
            raise UploadError("Mock 모드에는 가짜 전송 함수가 필요합니다.")
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
        body = self.encode(records)
        status, _ = self._perform(lambda: Request(
            self.endpoint, data=body, method=self.method, headers={
                "Content-Type": "application/json", **self.auth_headers(token),
            }), "업로드")
        return status

    def fetch(self, token):
        if not token:
            raise UploadError("저장된 인증 토큰이 없습니다.")
        _, payload = self._perform(lambda: Request(
            self.endpoint, method="GET", headers=self.auth_headers(token)), "기존 기록 다운로드")
        try:
            return json.loads(payload.decode("utf-8-sig"))
        except (ValueError, UnicodeError):
            raise UploadError("서버 기존 기록의 JSON 형식이 올바르지 않습니다.") from None

    def _perform(self, request_factory, operation):
        for attempt in range(self.retries + 1):
            try:
                with self.transport(request_factory(), timeout=self.timeout) as response:
                    status = response.status
                    payload = response.read()
            except HTTPError as exc:
                status = exc.code
                payload = b""
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < self.retries:
                    self.sleeper(min(2 ** attempt, 4))
                    continue
                raise UploadError(f"네트워크 연결 또는 응답 시간 초과로 {operation}에 실패했습니다.") from None
            if 200 <= status < 300:
                return status, payload
            if 500 <= status < 600 and attempt < self.retries:
                self.sleeper(min(2 ** attempt, 4))
                continue
            raise UploadError(f"{operation}이 거부되었습니다. HTTP {status}")
        raise UploadError(f"{operation} 재시도 횟수를 초과했습니다.")
