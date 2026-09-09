"""Attachment upload contracts (PLAN-ATTACHMENTS v1.1, ATT-01…ATT-20).

Every test uses a byte-identical copy of ``tests/fixtures/vault`` inside
``tmp_path`` or a fresh throwaway directory; the real user Vault is never
touched.  Watchers are off unless a test explicitly needs event delivery.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import pytest
from tests.backend.client import TestClient

from server.vault import atomic_write as atomic_write_module
from server.vault.attachments import (
    MAX_BASENAME_BYTES,
    clean_attachment_basename,
    is_image_attachment,
    normalize_target_directory,
    safe_attachment_name,
)
from server.vault.errors import AlreadyExists, FileTooLarge, InvalidRequest
from server.vault.service import VaultService

API = "/api/v1/vault/attachments"
RESOURCE = "/api/v1/vault/resource"


def _client(vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object, **kwargs: object) -> TestClient:
    service: VaultService = vault_service_factory(vault_fixture_copy, **kwargs)  # type: ignore[call-arg]
    return vault_api_client(service=service)  # type: ignore[call-arg]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _post(client: TestClient, name: str, directory: str, data: bytes) -> object:
    return client.post(API, json={"original_name": name, "target_directory": directory, "content_base64": _b64(data)})


def _error(response: object) -> dict[str, object]:
    body = response.json()  # type: ignore[attr-defined]
    assert set(body.keys()) == {"error"}, body
    assert set(body["error"].keys()) == {"code", "message", "path"}, body
    return body["error"]


# ---------------------------------------------------------------------------
# ATT-01 — naming rules (pure functions)
# ---------------------------------------------------------------------------


def test_clean_basename_folds_whitespace_and_dangerous_characters() -> None:
    # Whitespace folds to "-", the trailing dot before the extension is
    # stripped, and every separator/dangerous character becomes "-".
    assert clean_attachment_basename("  My  Photo  .png ") == "My-Photo.png"
    # Only the final path segment is a naming input; its separators become "-".
    assert clean_attachment_basename("a/b\\c:d*e?f.png") == "c-d-e-f.png"
    assert clean_attachment_basename("weird\u0000name\u0007.txt") == "weirdname.txt"
    assert clean_attachment_basename("a----b.txt") == "a-b.txt"


def test_clean_basename_keeps_readable_unicode() -> None:
    assert clean_attachment_basename("中文 图片 😀.png") == "中文-图片-😀.png"
    assert clean_attachment_basename("café résumé.pdf") == "café-résumé.pdf"


def test_clean_basename_rejects_hidden_and_windows_names() -> None:
    assert clean_attachment_basename(".env") == "env"
    assert clean_attachment_basename("..") == "attachment"
    assert clean_attachment_basename("...") == "attachment"
    assert clean_attachment_basename("") == "attachment"
    assert clean_attachment_basename("   ") == "attachment"
    assert clean_attachment_basename("CON.txt").startswith("_CON")
    assert clean_attachment_basename("nul") == "_nul"
    assert not clean_attachment_basename("trailing.  ").endswith(".")


def test_clean_basename_without_extension_and_unsafe_extension() -> None:
    assert clean_attachment_basename("noextension") == "noextension"
    # Only the last path segment survives; an embedded separator is folded.
    assert clean_attachment_basename("script.p/ng") == "ng"
    assert clean_attachment_basename("dir/sub/file.png") == "file.png"
    # An extension longer than the allowed shape is not treated as one.
    assert clean_attachment_basename("a." + "x" * 40) == "a." + "x" * 40


def test_clean_basename_limits_utf8_bytes_without_splitting_characters() -> None:
    name = clean_attachment_basename("😀" * 200 + ".png")
    encoded = name.encode("utf-8")
    assert len(encoded) <= MAX_BASENAME_BYTES
    assert encoded.decode("utf-8") == name  # no half character
    assert name.endswith(".png")


def test_safe_attachment_name_joins_validated_directory() -> None:
    assert safe_attachment_name("photo.png", "notes/2026") == "notes/2026/photo.png"
    assert safe_attachment_name("photo.png", "") == "photo.png"
    assert safe_attachment_name("photo.png", ".") == "photo.png"
    assert safe_attachment_name("photo.png", "a/b/c") == "a/b/c/photo.png"


def test_safe_attachment_name_increments_on_collision() -> None:
    existing = ["photo.png", "photo-2.png"]
    assert safe_attachment_name("photo.png", "notes", existing) == "notes/photo-3.png"
    assert safe_attachment_name("photo.png", "notes", ["other.png"]) == "notes/photo.png"
    # Only the last segment is compared.
    assert safe_attachment_name("photo.png", "notes", ["deep/dir/photo.png"]) == "notes/photo-2.png"


def test_safe_attachment_name_digest_suffix_is_optional_hint() -> None:
    digest = "sha256:" + "ab12cd" + "0" * 58
    assert safe_attachment_name("photo.png", "", [], digest) == "photo-ab12cd.png"
    # With the digest variant taken, the plain name is used before numbers.
    assert safe_attachment_name("photo.png", "", ["photo-ab12cd.png"], digest) == "photo.png"
    assert safe_attachment_name("photo.png", "", ["photo-ab12cd.png", "photo.png"], digest) == "photo-2.png"
    # A malformed digest is ignored rather than trusted.
    assert safe_attachment_name("photo.png", "", [], "not-a-digest") == "photo.png"


@pytest.mark.parametrize(
    "value",
    ["../out", "a/../../b", "/abs", "\\abs", "C:/x", "//unc/share", "a//b", "a/./b", "notes/.hidden", ".localnote", ".localnote/x", "a\x00b"],
)
def test_normalize_target_directory_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(Exception):
        normalize_target_directory(value)


def test_normalize_target_directory_accepts_root_and_plain_directories() -> None:
    assert normalize_target_directory("") == ""
    assert normalize_target_directory(".") == ""
    assert normalize_target_directory("notes") == "notes"
    assert normalize_target_directory("notes/2026") == "notes/2026"


def test_is_image_attachment_prefers_mime_then_extension() -> None:
    assert is_image_attachment("x.bin", "image/png") is True
    assert is_image_attachment("x.PNG", None) is True
    assert is_image_attachment("x.pdf", "application/pdf") is False


# ---------------------------------------------------------------------------
# ATT-02 — streaming no-overwrite primitive
# ---------------------------------------------------------------------------


def test_atomic_create_stream_writes_bytes_and_digest(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    payload = b"\x00\x01\x02BOM\xef\xbb\xbf\xff" * 1000
    digest, length = atomic_write_module.atomic_create_stream(
        target,
        [payload[index : index + 97] for index in range(0, len(payload), 97)],
        max_bytes=10**7,
    )
    assert target.read_bytes() == payload
    assert length == len(payload)
    assert digest == f"sha256:{hashlib.sha256(payload).hexdigest()}"
    assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]


def test_atomic_create_stream_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    target.write_bytes(b"original")
    with pytest.raises(AlreadyExists):
        atomic_write_module.atomic_create_stream(target, [b"replacement"], max_bytes=100)
    assert target.read_bytes() == b"original"


def test_atomic_create_stream_aborts_and_cleans_up_over_limit(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    seen: list[Path] = []
    with pytest.raises(FileTooLarge):
        atomic_write_module.atomic_create_stream(
            target,
            [b"x" * 10, b"y" * 10, b"z" * 10],
            max_bytes=15,
            on_temp=seen.append,
        )
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []
    assert seen and seen[0].name.startswith(".localnote-tmp-")


def test_atomic_create_stream_cleans_up_when_stream_raises(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"

    def broken() -> object:
        yield b"partial"
        raise RuntimeError("client disconnected")

    with pytest.raises(RuntimeError):
        atomic_write_module.atomic_create_stream(target, broken(), max_bytes=10**6)  # type: ignore[arg-type]
    assert list(tmp_path.iterdir()) == []


def test_iter_chunks_reads_bounded_blocks() -> None:
    class Recorder:
        def __init__(self, data: bytes) -> None:
            self.data = data
            self.reads: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.reads.append(size)
            chunk, self.data = self.data[:size], self.data[size:]
            return chunk

    stream = Recorder(b"abcdefghij")
    assert list(atomic_write_module.iter_chunks(stream, chunk_size=4)) == [b"abcd", b"efgh", b"ij"]
    assert set(stream.reads) == {4}


# ---------------------------------------------------------------------------
# ATT-03/05 — JSON upload endpoint
# ---------------------------------------------------------------------------


def test_json_upload_lands_in_existing_directory_and_round_trips(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    payload = b"\xef\xbb\xbf\x00\x01\x02binary\xff\xfe"
    response = _post(client, "My Photo 测试 😀.png", "notes", payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body.keys()) == {"path", "sha256", "byte_length", "content_type", "operation", "original_name"}
    assert body["path"] == "notes/My-Photo-测试-😀.png"
    assert body["operation"] == "created"
    assert body["original_name"] == "My Photo 测试 😀.png"
    assert body["byte_length"] == len(payload)
    assert body["sha256"] == f"sha256:{hashlib.sha256(payload).hexdigest()}"
    assert body["content_type"] == "image/png"
    assert (vault_fixture_copy / body["path"]).read_bytes() == payload


def test_json_upload_to_vault_root(vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = _post(client, "root.txt", "", b"root")
    assert response.status_code == 201, response.text
    assert response.json()["path"] == "root.txt"
    assert (vault_fixture_copy / "root.txt").read_bytes() == b"root"


def test_json_upload_does_not_overwrite_and_dedupes(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    first = _post(client, "dup.txt", "notes", b"one")
    second = _post(client, "dup.txt", "notes", b"two")
    assert first.status_code == 201 and second.status_code == 201, second.text
    assert first.json()["path"] == "notes/dup.txt"
    assert second.json()["path"] == "notes/dup-2.txt"
    assert (vault_fixture_copy / "notes" / "dup.txt").read_bytes() == b"one"
    assert (vault_fixture_copy / "notes" / "dup-2.txt").read_bytes() == b"two"


def test_json_upload_missing_directory_is_not_created(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = _post(client, "x.txt", "missing/2026", b"x")
    assert response.status_code == 404
    assert _error(response)["code"] == "not_found"
    assert not (vault_fixture_copy / "missing").exists()


def test_json_upload_rejects_file_as_directory(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = _post(client, "x.txt", "attachments/image.png", b"x")
    assert response.status_code in (400, 404)
    assert _error(response)["code"] in {"not_a_directory", "not_found"}


@pytest.mark.parametrize("directory", ["../out", "/abs", "a/../../b", "notes/.hidden", ".localnote"])
def test_json_upload_rejects_unsafe_target_directory(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object, directory: str
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = _post(client, "x.txt", directory, b"x")
    assert response.status_code == 400, directory
    assert _error(response)["code"] in {"path_traversal", "invalid_request"}


def test_json_upload_rejects_bad_fields(vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    assert client.post(API, json={"original_name": "  ", "target_directory": "", "content_base64": _b64(b"x")}).status_code == 422
    assert client.post(API, json={"original_name": "x", "target_directory": "", "content_base64": "not base64!"}).status_code == 400
    assert client.post(API, json={"original_name": "x", "target_directory": "", "content_base64": _b64(b"x"), "extra": 1}).status_code == 422
    assert client.post(API, json={"target_directory": "", "content_base64": _b64(b"x")}).status_code == 422
    assert client.post(API, json={"original_name": "x", "content_base64": _b64(b"x")}).status_code == 422


def test_json_upload_oversize_is_413(vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client, max_file_bytes=32)
    response = _post(client, "big.bin", "notes", b"x" * 33)
    assert response.status_code == 413
    assert _error(response)["code"] == "file_too_large"
    assert not (vault_fixture_copy / "notes" / "big.bin").exists()


def test_json_upload_vault_not_configured(vault_api_client: object) -> None:
    client = vault_api_client()  # type: ignore[call-arg]
    response = client.post(API, json={"original_name": "x", "target_directory": "", "content_base64": _b64(b"x")})
    assert response.status_code == 503
    assert _error(response)["code"] == "vault_not_configured"


def test_json_upload_does_not_leak_root_or_traceback(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = _post(client, "x.txt", "missing", b"x")
    text = response.text
    assert str(vault_fixture_copy) not in text
    assert "Traceback" not in text


# ---------------------------------------------------------------------------
# ATT-06 — multipart streaming endpoint
# ---------------------------------------------------------------------------


def test_multipart_upload_streams_and_lands(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    payload = b"stream-payload" * 5000
    response = client.post(
        f"{API}/multipart",
        files={"file": ("big.bin", payload, "application/octet-stream")},
        data={"target_directory": "notes", "original_name": "报告 final.bin"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["path"] == "notes/报告-final.bin"
    assert body["byte_length"] == len(payload)
    assert (vault_fixture_copy / body["path"]).read_bytes() == payload


def test_multipart_upload_defaults_original_name_from_filename(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = client.post(
        f"{API}/multipart",
        files={"file": ("photo.png", b"png-bytes", "image/png")},
        data={"target_directory": ""},
    )
    assert response.status_code == 201, response.text
    assert response.json()["path"] == "photo.png"
    assert response.json()["content_type"] == "image/png"


def test_multipart_upload_reads_in_bounded_chunks(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    """The service must never aggregate the payload with an unbounded read."""
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    client = vault_api_client(service=service)  # type: ignore[call-arg]
    payload = b"y" * 300_000
    reads: list[int] = []

    class Spy:
        def __init__(self, data: bytes) -> None:
            self.data = data

        def read(self, size: int = -1) -> bytes:
            reads.append(size)
            assert size > 0, "unbounded read would buffer the whole upload"
            chunk, self.data = self.data[:size], self.data[size:]
            return chunk

    result = service.upload_attachment_stream("spy.bin", "notes", Spy(payload))
    assert result["byte_length"] == len(payload)
    assert reads and all(size <= 1024 * 1024 for size in reads)
    assert len(reads) >= 1
    assert (vault_fixture_copy / result["path"]).read_bytes() == payload
    assert client is not None


def test_multipart_upload_missing_file_field_is_400(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = client.post(f"{API}/multipart", data={"target_directory": ""})
    assert response.status_code in (400, 422)


def test_multipart_upload_missing_target_directory_falls_back_to_root(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    """A browser omits an empty ``target_directory``; that means the Vault root."""
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = client.post(f"{API}/multipart", files={"file": ("x.bin", b"x")})
    assert response.status_code == 201, response.text
    assert response.json()["path"] == "x.bin"


def test_multipart_upload_oversize_cleans_up(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client, max_file_bytes=64)
    response = client.post(
        f"{API}/multipart",
        files={"file": ("big.bin", b"z" * 200)},
        data={"target_directory": "notes"},
    )
    assert response.status_code == 413
    assert _error(response)["code"] == "file_too_large"
    leftovers = [p.name for p in (vault_fixture_copy / "notes").iterdir() if p.name.startswith(".localnote-tmp-")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# ATT-07 — read-only resource endpoint
# ---------------------------------------------------------------------------


def test_resource_returns_raw_bytes_with_headers(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    created = _post(client, "pre view 图片.png", "notes", b"\x89PNG\r\n\x1a\nbytes")
    path = created.json()["path"]
    response = client.get(RESOURCE, params={"path": path})
    assert response.status_code == 200, response.text
    assert response.content == b"\x89PNG\r\n\x1a\nbytes"
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-length"] == str(len(response.content))
    assert response.headers["content-disposition"].startswith("inline;")
    # The header must stay latin-1 encodable (RFC 5987 filename*).
    response.headers["content-disposition"].encode("latin-1")
    assert "%E5%9B%BE%E7%89%87" in response.headers["content-disposition"]


def test_resource_rejects_unsafe_and_missing_paths(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    assert client.get(RESOURCE, params={"path": "../escape"}).status_code == 400
    assert client.get(RESOURCE, params={"path": "/etc/passwd"}).status_code == 400
    assert client.get(RESOURCE, params={"path": ".localnote/state"}).status_code == 400
    assert client.get(RESOURCE, params={"path": "nope.png"}).status_code == 404


def test_resource_rejects_directory_and_symlink(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    outside = vault_fixture_copy.parent / "outside.png"
    outside.write_bytes(b"secret")
    (vault_fixture_copy / "link.png").symlink_to(outside)
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    assert client.get(RESOURCE, params={"path": "link.png"}).status_code == 400
    assert client.get(RESOURCE, params={"path": "notes"}).status_code in (400, 404)
    assert client.get(RESOURCE, params={"path": "link.png"}).text.find(str(outside)) == -1


def test_resource_head_reuses_service_metadata(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    created = _post(client, "head.bin", "notes", b"0123456789")
    response = client.head(RESOURCE, params={"path": created.json()["path"]})
    assert response.status_code == 200
    assert response.headers["content-length"] == "10"
    assert response.content == b""


def test_resource_unknown_mime_is_octet_stream(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    created = _post(client, "data.unknownext", "notes", b"payload")
    response = client.get(RESOURCE, params={"path": created.json()["path"]})
    assert response.headers["content-type"].startswith("application/octet-stream")


def test_resource_oversize_is_413(vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object) -> None:
    (vault_fixture_copy / "huge.bin").write_bytes(b"x" * 100)
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client, max_file_bytes=50)
    response = client.get(RESOURCE, params={"path": "huge.bin"})
    assert response.status_code == 413
    assert _error(response)["code"] == "file_too_large"


def test_resource_does_not_execute_html(vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object) -> None:
    (vault_fixture_copy / "page.html").write_bytes(b"<script>alert(1)</script>")
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = client.get(RESOURCE, params={"path": "page.html"})
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------------------
# ATT-03/20 — security: symlinks, target races, hidden/derived, listing
# ---------------------------------------------------------------------------


def test_upload_rejects_symlink_target_directory(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    outside = vault_fixture_copy.parent / "outside"
    outside.mkdir()
    (vault_fixture_copy / "linked").symlink_to(outside, target_is_directory=True)
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = _post(client, "x.txt", "linked", b"x")
    assert response.status_code == 400
    assert _error(response)["code"] == "symlink_escape"
    assert list(outside.iterdir()) == []


def test_upload_rejects_target_that_is_a_symlink(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    outside = vault_fixture_copy.parent / "secret.txt"
    outside.write_bytes(b"secret")
    (vault_fixture_copy / "notes" / "trap.txt").symlink_to(outside)
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    # Pre-check sees the symlink and refuses; it must never replace the link.
    with pytest.raises(Exception):
        service.upload_attachment_bytes("trap.txt", "notes", b"new")
    assert outside.read_bytes() == b"secret"


def test_upload_uses_next_name_when_commit_races(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    """A concurrent creator that wins the name must never be overwritten.

    The race is injected at the no-overwrite commit itself (``os.link``), so
    the temporary stream has already been written and the service must retry
    with the next name without losing or overwriting any bytes.
    """
    import server.vault.atomic_write as atomic_write_module

    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    original_link = atomic_write_module.os.link
    attempts: list[str] = []

    def racing_link(src: object, dst: object, **kwargs: object) -> None:
        attempts.append(Path(dst).name)  # type: ignore[arg-type]
        if len(attempts) == 1:
            # An external writer wins the name between pre-check and commit.
            Path(dst).write_bytes(b"external")  # type: ignore[arg-type]
        return original_link(src, dst, **kwargs)

    atomic_write_module.os.link = racing_link  # type: ignore[assignment]
    try:
        result = service.upload_attachment_stream("race.bin", "notes", b"mine")
    finally:
        atomic_write_module.os.link = original_link  # type: ignore[assignment]
    assert attempts[0] == "race.bin"
    assert result["path"] == "notes/race-2.bin"
    assert result["byte_length"] == 4
    assert (vault_fixture_copy / "notes" / "race.bin").read_bytes() == b"external"
    assert (vault_fixture_copy / "notes" / "race-2.bin").read_bytes() == b"mine"


def test_upload_does_not_create_hidden_or_derived_paths(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    for directory in (".localnote", "notes/.git", "notes/..hidden"):
        response = _post(client, "x.txt", directory, b"x")
        assert response.status_code == 400, directory
    assert not (vault_fixture_copy / ".localnote").exists() or (vault_fixture_copy / ".localnote").is_dir()


def test_upload_does_not_leak_temporary_files_into_listing(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    client = vault_api_client(service=service)  # type: ignore[call-arg]
    _post(client, "listed.txt", "notes", b"listed")
    paths = {entry.path for entry in service.list_tree(include_hidden=True)}
    assert "notes/listed.txt" in paths
    assert not any(".localnote-tmp-" in path for path in paths)


def test_upload_watcher_drops_temporary_names(vault_fixture_copy: Path, vault_service_factory: object) -> None:
    """Service temp files and hidden segments never reach watcher consumers."""
    events: list[object] = []
    service: VaultService = vault_service_factory(  # type: ignore[call-arg]
        vault_fixture_copy,
        watcher_enabled=True,
        event_callback=events.append,
    )
    service.upload_attachment_bytes("watched.bin", "notes", b"watched")
    watcher = service.watcher
    assert watcher is not None
    assert watcher._normalize_os_path(str(vault_fixture_copy / "notes" / "watched.bin")) == "notes/watched.bin"
    assert watcher._normalize_os_path(str(vault_fixture_copy / "notes" / ".localnote-tmp-123-abc")) is None
    assert watcher._normalize_os_path(str(vault_fixture_copy / ".localnote" / "index.db")) is None
    assert watcher._normalize_os_path(str(vault_fixture_copy / "notes" / ".hidden")) is None
    assert watcher._normalize_os_path(str(vault_fixture_copy.parent)) is None


def test_upload_rejects_original_name_that_is_not_a_string(
    vault_fixture_copy: Path, vault_service_factory: object
) -> None:
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    with pytest.raises(InvalidRequest):
        service.upload_attachment_bytes(123, "", b"x")  # type: ignore[arg-type]
    with pytest.raises(InvalidRequest):
        service.upload_attachment_bytes("   ", "", b"x")


def test_upload_stream_rejects_non_bytes_chunks(vault_fixture_copy: Path, vault_service_factory: object) -> None:
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    with pytest.raises(Exception):
        service.upload_attachment_stream("bad.bin", "notes", ["not-bytes"])
    assert not (vault_fixture_copy / "notes" / "bad.bin").exists()


def test_upload_concurrent_same_name_produces_unique_files(vault_fixture_copy: Path, vault_service_factory: object) -> None:
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    results = [service.upload_attachment_bytes("same.bin", "notes", f"body-{index}".encode()) for index in range(3)]
    paths = [result["path"] for result in results]
    assert len(set(paths)) == 3
    assert set(paths) == {"notes/same.bin", "notes/same-2.bin", "notes/same-3.bin"}
    for result in results:
        assert (vault_fixture_copy / result["path"]).read_bytes() == f"body-{paths.index(result['path'])}".encode()


def test_upload_bytes_over_service_limit_raises(vault_fixture_copy: Path, vault_service_factory: object) -> None:
    service: VaultService = vault_service_factory(vault_fixture_copy, max_file_bytes=8)  # type: ignore[call-arg]
    with pytest.raises(FileTooLarge):
        service.upload_attachment_bytes("x.bin", "notes", b"0123456789")


def test_upload_does_not_touch_outside_root(vault_fixture_copy: Path, vault_service_factory: object) -> None:
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    before = sorted(p.name for p in vault_fixture_copy.parent.iterdir())
    service.upload_attachment_bytes("ok.txt", "notes", b"ok")
    after = sorted(p.name for p in vault_fixture_copy.parent.iterdir())
    assert before == after


# ---------------------------------------------------------------------------
# ATT-17/18 — index/wikilink compatibility and the Policy boundary
# ---------------------------------------------------------------------------


def test_attachment_is_not_a_graph_node(
    vault_fixture_copy: Path, vault_service_factory: object, index_service_factory: object
) -> None:
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    created = service.upload_attachment_bytes("graph.png", "notes", b"png")
    (vault_fixture_copy / "notes" / "refers.md").write_text(f"![alt]({created['path'].split('/')[-1]})\n", encoding="utf-8")
    index = index_service_factory(service)  # type: ignore[call-arg]
    paths = {entry.path for entry in index.entries()}
    assert "notes/refers.md" in paths
    # Attachments are not Markdown notes and never become graph/index nodes.
    assert created["path"] not in paths
    snapshot = index.graph_snapshot()
    assert created["path"] not in {note.path for note in snapshot.notes}


def test_relative_attachment_reference_does_not_break_links(
    vault_fixture_copy: Path, vault_service_factory: object, index_service_factory: object
) -> None:
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    service.upload_attachment_bytes("diagram.png", "notes", b"png")
    (vault_fixture_copy / "notes" / "with-attachment.md").write_text(
        "![diagram](diagram.png)\n\n[[Alpha]]\n", encoding="utf-8"
    )
    index = index_service_factory(service)  # type: ignore[call-arg]
    targets = {ref.target for ref in index.outgoing_for("notes/with-attachment.md")}
    assert "Alpha" in targets
    assert "diagram.png" not in targets


def test_policy_attachment_write_still_denied_for_agents() -> None:
    from server.actions.schemas import Action, ActionType
    from server.policies.engine import ActionFacts, PolicyEngine

    engine = PolicyEngine()
    for level in (1, 2):
        action = Action(
            action=ActionType.CREATE_NOTE,
            permission_level=level,
            file="notes/agent.md",
            content_base64="",
            reason="agent attachment attempt",
        )
        result = engine.evaluate(action, facts=ActionFacts(existing=False, attachment=True), level=level)
        assert result.decision == "deny"
        assert "attachment_write" in result.matched_rules


def test_user_upload_path_does_not_invoke_policy(
    vault_fixture_copy: Path, vault_service_factory: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The direct-upload path must never consult the Policy engine."""
    import server.policies.engine as engine_module

    def explode(*args: object, **kwargs: object) -> object:
        raise AssertionError("PolicyEngine must not be used for user uploads")

    monkeypatch.setattr(engine_module.PolicyEngine, "evaluate", explode)
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    result = service.upload_attachment_bytes("direct.png", "notes", b"png")
    assert result["operation"] == "created"
    assert (vault_fixture_copy / result["path"]).read_bytes() == b"png"


def test_existing_five_vault_endpoints_unchanged(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    created = client.post("/api/v1/vault/file", json={"path": "notes/new.md", "content_base64": _b64(b"# hi")})
    assert created.status_code == 201 and created.json()["operation"] == "created"
    read = client.get("/api/v1/vault/file", params={"path": "notes/new.md"})
    assert read.status_code == 200 and read.json()["path"] == "notes/new.md"
    patched = client.patch(
        "/api/v1/vault/file",
        json={"path": "notes/new.md", "content_base64": _b64(b"# hi2"), "expected_sha256": read.json()["sha256"]},
    )
    assert patched.status_code == 200 and patched.json()["operation"] == "updated"
    moved = client.post(
        "/api/v1/vault/file/move",
        json={"source_path": "notes/new.md", "destination_path": "notes/moved.md", "expected_sha256": patched.json()["sha256"]},
    )
    assert moved.status_code == 200 and moved.json()["operation"] == "moved"
    deleted = client.request(
        "DELETE",
        "/api/v1/vault/file",
        json={"path": "notes/moved.md", "expected_sha256": moved.json()["sha256"]},
    )
    assert deleted.status_code == 200 and deleted.json()["operation"] == "deleted"
    listed = client.get("/api/v1/vault/files")
    assert listed.status_code == 200


def test_upload_endpoints_are_not_writable_by_directories(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    """No new write path may accept an absolute path or a Path object."""
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = client.post(
        API,
        json={"original_name": "x.txt", "target_directory": str(vault_fixture_copy), "content_base64": _b64(b"x")},
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "path_traversal"


def test_upload_response_never_exposes_absolute_root(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = _post(client, "safe.txt", "notes", b"x")
    assert str(vault_fixture_copy) not in response.text
    assert not response.json()["path"].startswith(("/", os.path.sep))
    assert ".." not in response.json()["path"].split("/")


def test_uploaded_file_is_readable_by_the_existing_file_endpoint(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    created = _post(client, "round.txt", "notes", b"round-trip")
    read = client.get("/api/v1/vault/file", params={"path": created.json()["path"]})
    assert read.status_code == 200
    assert base64.b64decode(read.json()["content_base64"]) == b"round-trip"
    assert read.json()["sha256"] == created.json()["sha256"]
