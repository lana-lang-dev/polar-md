"""Build a Python-only source distribution ZIP without installed dependencies."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root=Path(__file__).resolve().parents[1]
out=root/'releases'/'bear-markdown-desktop.zip'
out.parent.mkdir(exist_ok=True)
files=[root/name for name in ['README.md','requirements.txt','pyproject.toml','run.py']]
for folder in ['bear_editor','tests_python','scripts']:
    files.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
with ZipFile(out,'w',ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path,Path('bear-markdown-desktop')/path.relative_to(root))
print(out)
