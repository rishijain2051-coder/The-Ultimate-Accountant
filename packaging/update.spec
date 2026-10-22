# Update.exe - the app's own code alone, a few MB, for installed copies whose
# base (Python, the libraries, the OCR models) is the one it was built on.
#
# The same installer.py as Install.exe; its payload is _internal/app and the
# BASE it was built on, not the whole program, so it swaps that folder only
# (installer.update_app). packaging/build_release.py lays the payload out in
# dist/update-payload before this runs.

a = Analysis(
    ['installer.py'],
    pathex=['..'],
    datas=[('../dist/update-payload', 'payload')],
    excludes=['numpy', 'cv2', 'onnxruntime', 'fastapi', 'uvicorn', 'openpyxl',
              'reportlab', 'PIL', 'tkinter', 'pandas', 'scipy'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='Update',
    console=True,          # says what it is doing, and when it is done
    icon='../app/icon.ico',
    upx=False,
)
