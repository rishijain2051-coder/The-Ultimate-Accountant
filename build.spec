# PyInstaller build for Ultimate Accountant: both modules, one program.
#
#   python -m PyInstaller build.spec --noconfirm
#
# A one-folder build, deliberately: a one-file build unpacks its 13 MB of OCR
# models into a temporary directory on every single launch, which turns a
# two-second start into a twenty-second one and writes the whole payload to
# disk each time. The folder is zipped for distribution instead.
#
# No console window - the app runs in a window of its own (launch.py), and
# anything the launcher needs to say before that opens goes to a dialog box.

#
# The app's own code is not frozen into the exe with everything else: it goes
# into _internal/app as plain files, so an update can carry that folder alone -
# a few MB - while Python, the libraries and the OCR models (the "base") stay
# as they are. _internal/BASE says what the base is, and an update is only
# ever put over the base it was built on (packaging/installer.py, Update.exe).

import hashlib
import json
import py_compile
import shutil
import sys
from importlib import metadata
from pathlib import Path

import PyInstaller
from PyInstaller.utils.hooks import (collect_data_files, collect_dynamic_libs,
                                     collect_submodules)

# The OCR models ship as data inside the wheel; without this the exe builds
# fine and then fails the moment somebody drops in a scan.
datas = [
    ('SampleData', 'SampleData'),
]
datas += collect_data_files('rapidocr_onnxruntime')

# rapidocr appends its own directory to sys.path and imports its three
# sub-packages by bare name - `import ch_ppocr_v3_det`, not
# `rapidocr_onnxruntime.ch_ppocr_v3_det`. Collecting them as submodules below
# puts them in the archive under their qualified names; `_alias_bare_modules`
# in app/backend/docs.py registers the bare names that rapidocr actually asks
# for. Copying the package directory as data does NOT work - PyInstaller
# strips .py files out of data - and without the alias the exe starts, reports
# OCR as available, then fails on the first scan with
#   module 'ch_ppocr_v3_det' has no attribute 'TextDetector'
datas += collect_data_files('pdfplumber')
datas += collect_data_files('pypdfium2')
datas += collect_data_files('reportlab')          # the vendored Type 1 fonts
# The app window: pywebview's WebView2 libraries, and the scripts it reads from
# disk into every page - the standard hook takes only the libraries, and
# without the scripts each page load fails inside pywebview.
datas += collect_data_files('webview')
# The Invoice Bridge reads PDFs with PyMuPDF, which brings its own libraries.
datas += collect_data_files('pymupdf')
# The deployer's settings (app/backend/deploy.py, docs/configuration.md): the
# release page, its signing key's public half, the control plane. Public values
# only - never a secret. packaging/build_release.py refuses to make a release
# without the first two, unless asked for a copy that never updates.
if Path('deploy.env').exists():
    datas.append(('deploy.env', '.'))

binaries = collect_dynamic_libs('onnxruntime') + collect_dynamic_libs('pypdfium2') + collect_dynamic_libs('pymupdf')

# Everything reached only by string name or plugin lookup, which the analysis
# cannot see: uvicorn's protocol implementations, and the OCR stack.
hiddenimports = [
    'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.loops.asyncio',
    'uvicorn.protocols.http.auto', 'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'rapidocr_onnxruntime', 'onnxruntime', 'onnxruntime.capi',
    'onnxruntime.capi.onnxruntime_pybind11_state',
    'pypdfium2', 'pdfplumber', 'rapidfuzz',
    'multipart', 'python_multipart',
    'app.backend.server', 'app.main', 'app.bridge.main',
    # The Invoice Bridge's database, picked by the URL's name at run time.
    'sqlalchemy.dialects.sqlite', 'sqlalchemy.dialects.sqlite.pysqlite',
    'pymupdf', 'pydantic_settings', 'httpx',
]
# Both import styles, so it works whichever way rapidocr reaches for them.
hiddenimports += collect_submodules('rapidocr_onnxruntime')
hiddenimports += ['shapely', 'pyclipper']

binaries += collect_dynamic_libs('shapely')

a = Analysis(
    ['launch.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Nothing here draws a chart or a window server-side, and each of these
    # drags in tens of megabytes. pandas, scipy and pyarrow are in the
    # environment but nothing in this app imports them - only numpy is
    # actually used, for the OCR image arrays - so they are dropped rather
    # than shipped. That is roughly 250 MB of the download.
    # pywebview's other platforms: only the Windows one (WinForms and WebView2) runs here.
    excludes=['tkinter', 'matplotlib', 'IPython', 'notebook', 'pytest',
              'PyQt5', 'PySide2', 'PIL.ImageQt',
              'pandas', 'scipy', 'pyarrow', 'setuptools',
              'cv2.gapi', 'torch', 'sympy',
              'webview.platforms.qt', 'webview.platforms.gtk', 'webview.platforms.cocoa',
              'webview.platforms.android', 'webview.platforms.cef'],
    noarchive=False,
)

# OpenCV ships video codecs with it. The OCR engine uses it to resize and
# threshold an image and nothing else, so 56 MB of ffmpeg is pure download
# weight.
#
# Do NOT add the OpenBLAS libraries here. numpy ships its own copy under a
# `libscipy_openblas` name, so a filter aimed at scipy takes numpy's C
# extension down with it - the exe still starts and serves, and then every
# OCR read fails with "DLL load failed while importing _multiarray_umath".
_UNUSED_BINARY = ('opencv_videoio_ffmpeg', 'mkl_')
a.binaries = TOC([entry for entry in a.binaries
                  if not any(tag.lower() in entry[0].lower()
                             for tag in _UNUSED_BINARY)])

# The app's own modules stay out of the archive; they are copied as files below.
_ours = [e for e in a.pure if e[0] == 'app' or e[0].startswith('app.')]
a.pure = [e for e in a.pure if e not in _ours]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Ultimate Accountant',
    debug=False,
    strip=False,
    upx=False,
    console=False,                 # closed from the interface, not a terminal
    icon='app/icon.ico',           # brand/ultimate-accountant: the app tile, 16-256 px
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Ultimate Accountant',
)

# The app's own code, as files: each module the analysis found, with a .pyc
# checked by the source's hash rather than its date, then the interface and
# the export rules.
_internal = Path(DISTPATH) / 'Ultimate Accountant' / '_internal'
for name, src, _ in _ours:
    if not src or not str(src).endswith('.py'):
        continue                                   # 'app' itself: a folder, no file of its own
    rel = Path(*name.split('.'))
    dst = _internal / (rel / '__init__.py' if Path(src).name == '__init__.py' else rel.with_suffix('.py'))
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    py_compile.compile(str(dst), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
shutil.copytree('app/frontend', _internal / 'app' / 'frontend', dirs_exist_ok=True)
shutil.copy2('app/backend/exim_rules.json', _internal / 'app' / 'backend' / 'exim_rules.json')
shutil.copy2('app/home.html', _internal / 'app' / 'home.html')          # the choice of module
shutil.copy2('app/icon.ico', _internal / 'app' / 'icon.ico')            # the window's icon (launch.py)
# The Invoice Bridge's interface, built (packaging/build_release.py runs `npm run build`
# first), and the page phones send bills from: with the app's own code, so an update
# carries them too (app/bridge/config.py UI_DIR).
if not Path('bridge-ui/out/index.html').exists():
    sys.exit('bridge-ui/out is missing: run `npm run build` in bridge-ui first.')
shutil.copytree('bridge-ui/out', _internal / 'app' / 'bridge' / 'ui', dirs_exist_ok=True)
shutil.copy2('app/bridge/phone.html', _internal / 'app' / 'bridge' / 'phone.html')

# The base: what a light update has to find installed to go over it - the same
# Python, PyInstaller, bundled packages, launcher, recipe and sample data, and
# every module the new code needs already there.
def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

_modules = sorted({e[0] for e in a.pure} | {e[0] for e in a.binaries if e[2] == 'EXTENSION'})
_owners = metadata.packages_distributions()
_packages = sorted({f"{d}=={metadata.version(d)}" for m in _modules for d in _owners.get(m.split('.')[0], [])})
_sample = hashlib.sha256()
for _p in sorted(Path('SampleData').rglob('*')):
    if _p.is_file():
        _sample.update(_p.as_posix().encode() + _p.read_bytes())
_core = hashlib.sha256(json.dumps({
    'python': sys.version, 'pyinstaller': PyInstaller.__version__, 'packages': _packages,
    'launcher': _sha('launch.py'), 'recipe': _sha('build.spec'), 'sample': _sample.hexdigest(),
    'deploy': _sha('deploy.env') if Path('deploy.env').exists() else None,
}, sort_keys=True).encode()).hexdigest()[:16]
(_internal / 'BASE').write_text(json.dumps({
    'core': _core, 'about': f"Python {sys.version.split()[0]}, PyInstaller {PyInstaller.__version__}, "
                            f"{len(_packages)} packages", 'modules': _modules}, indent=0), encoding='utf-8')
print(f"\n  base {_core}: {len(_packages)} packages, {len(_modules)} modules; "
      f"{sum(1 for _ in (_internal / 'app').rglob('*.py'))} of the app's own files beside them")
