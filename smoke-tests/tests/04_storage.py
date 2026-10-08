"""File uploads."""


def test_upload(uploaded, detail):
    """Upload is accepted"""
    detail(f"HTTP {uploaded['status']}, {len(uploaded['content']) // 1024} KiB")
    assert uploaded["key"], "upload failed; is the storage directory writable by the backend?"


def test_download(stack, session, uploaded):
    """Download returns the same bytes"""
    response = session.get(f"{stack.base_url}/api/common/document/{uploaded['key']}", timeout=30)
    assert response.status_code == 200
    assert response.content == uploaded["content"]


def test_file_on_volume(backend, uploaded):
    """File is stored on the storage volume"""
    assert uploaded["stored"] and backend.stored_file(uploaded["stored"]) == uploaded["content"]
