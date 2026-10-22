# Uninstall.exe - a one-file console tool, placed inside the install folder.
a = Analysis(
    ['uninstaller.py'],
    pathex=['..'],
    excludes=['numpy', 'cv2', 'onnxruntime', 'fastapi', 'uvicorn', 'openpyxl',
              'reportlab', 'PIL', 'tkinter', 'pandas', 'scipy'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='Uninstall',
    console=True,
    icon='../app/icon.ico',
    upx=False,
)
