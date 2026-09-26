"""Unit tests for opaque Reader bearer-token authentication."""

import pytest

from server.reader.errors import ReaderError, ReaderErrorCode
from server.reader.router import get_device_id_from_token


class StubReaderService:
    def __init__(self, device_id: str | None):
        self.device_id = device_id
        self.seen_token: str | None = None

    def authenticate_access_token(self, token: str) -> str | None:
        self.seen_token = token
        return self.device_id


def test_bearer_token_resolves_through_registry():
    service = StubReaderService("device-a")
    assert get_device_id_from_token(service, "Bearer opaque-token", None) == "device-a"
    assert service.seen_token == "opaque-token"


def test_token_spelling_never_selects_device():
    service = StubReaderService(None)
    with pytest.raises(ReaderError) as exc_info:
        get_device_id_from_token(
            service,
            "Bearer reader_token_device-a_looks-authentic",
            None,
        )
    assert exc_info.value.code == ReaderErrorCode.UNAUTHORIZED
    assert service.seen_token == "reader_token_device-a_looks-authentic"


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Basic abc", "Bearer", "Bearer ", "Bearer a b"],
)
def test_invalid_authorization_is_rejected(authorization: str | None):
    service = StubReaderService("device-a")
    with pytest.raises(ReaderError) as exc_info:
        get_device_id_from_token(service, authorization, None)
    assert exc_info.value.code == ReaderErrorCode.UNAUTHORIZED
    assert service.seen_token is None
