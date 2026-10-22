# Install.exe - the single deliverable.
#
# Built last, because it carries the finished program folder inside itself as a
# payload. One file goes out; it places the app and the uninstaller.
#
# One-file here is the right trade, the opposite way round from the app: the
# unpack cost is paid once, at install, rather than on every launch.

a = Analysis(
    ['installer.py'],
    pathex=['..'],
    datas=[('../dist/Ultimate Accountant', 'payload')],
    excludes=['numpy', 'cv2', 'onnxruntime', 'fastapi', 'uvicorn', 'openpyxl',
              'reportlab', 'PIL', 'tkinter', 'pandas', 'scipy'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='Install',
    console=True,          # a few hundred megabytes of copying needs a progress bar
    icon='../app/icon.ico',
    upx=False,
)
