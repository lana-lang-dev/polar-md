from pathlib import Path
import pytest
from bear_editor.files import atomic_write, read_markdown, resolve_attachment, safe_url, write_bundle
from bear_editor.models import DocumentState
from bear_editor.storage import Storage


def test_unicode_and_line_endings(tmp_path):
    path = tmp_path / 'note.md'
    atomic_write(path, '# 日本語 🐻\r\n')
    assert read_markdown(path) == '# 日本語 🐻\r\n'


def test_safe_save_does_not_truncate(tmp_path):
    path = tmp_path / 'note.md'
    path.write_text('Original')
    with pytest.raises(ValueError, match='Save stopped'):
        atomic_write(path, '', True)
    assert path.read_text() == 'Original'


def test_failed_replace_leaves_original_and_no_temp(tmp_path, monkeypatch):
    path = tmp_path / 'note.md'
    path.write_text('Original')
    def fail(*args):
        raise OSError('Simulated disk failure')
    monkeypatch.setattr('bear_editor.files.os.replace', fail)
    with pytest.raises(OSError):
        atomic_write(path, 'New')
    assert path.read_text() == 'Original'
    assert len(list(tmp_path.iterdir())) == 1


def test_attachment_case_and_percent_paths(tmp_path):
    folder = tmp_path / 'Attachments'
    folder.mkdir()
    photo = folder / 'Photo One.JPG'
    photo.write_bytes(b'image')
    assert resolve_attachment('./attachments/photo%20one.jpg', str(tmp_path/'note.md'), {}) == photo


def test_protocol_security():
    for value in ['javascript:alert(1)', 'java\nscript:alert(1)', 'file:///etc/passwd', 'data:text/html,hi']:
        assert not safe_url(value)
    assert safe_url('notes://example')
    assert safe_url('Attachments/a.png', True)


def test_binary_rejected(tmp_path):
    path = tmp_path/'bad.md'
    path.write_bytes(b'\x00binary')
    with pytest.raises(ValueError, match='binary'):
        read_markdown(path)


def test_recovery_and_retention(tmp_path):
    storage = Storage(tmp_path)
    document = DocumentState(markdown='- [x] Done', saved_markdown='- [ ] Done', attachments={'Attachments/a.png':'/tmp/a.png'})
    storage.autosave(document)
    assert storage.load_draft().dirty
    assert storage.load_draft().markdown == '- [x] Done'
    assert storage.load_draft().attachments == document.attachments
    for i in range(35):
        document.markdown = f'Version {i}'
        storage.snapshot(document)
    storage.snapshot(document)
    assert len(storage.versions(document.id)) == 30
    document.markdown = ''
    storage.autosave(document)
    assert storage.load_draft().markdown == ''
    storage.close()


def test_bundle_includes_relative_asset(tmp_path):
    import zipfile
    image = tmp_path/'photo.png'
    image.write_bytes(b'png')
    bundle = tmp_path/'note.zip'
    write_bundle(bundle, '![x](Attachments/photo.png)', 'note.md', {'Attachments/photo.png': image})
    with zipfile.ZipFile(bundle) as zipped:
        assert set(zipped.namelist()) == {'note.md', 'Attachments/photo.png'}
