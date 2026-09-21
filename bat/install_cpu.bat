REM Create only the CPU virtual environment (.venv).
REM
REM This fork publishes the CPU build only: the CUDA package is ~3.4GB, over
REM GitHub's 2GB release asset limit, so it cannot be distributed here.
REM install.bat also builds .venv_cuda, which costs several GB of downloads
REM and a lot of CI time for an artifact this fork never ships.
REM
REM Use install.bat instead if you need the CUDA environment locally.
REM Keep this file ASCII-only: cmd.exe reads .bat in the console OEM code
REM page, not UTF-8 (see src-python/test/test_toolchain_file_encoding.py).

REM .venv exists
if exist .venv (
    rmdir /s /q .venv
)

REM make .venv
python -m venv .venv

REM install packages for .venv
call .venv/Scripts/activate
python.exe -m pip install --upgrade pip
pip install --no-cache-dir --force-reinstall -r requirements.txt
REM rapidocr requires opencv-python, but this app uses opencv-python-headless.
REM Both provide the same cv2 and collide, so reinstall it without its
REM dependencies (the dependencies are listed explicitly in requirements).
pip install --no-cache-dir --force-reinstall --no-deps rapidocr==3.9.2
python -X utf8 tools\fetch_ocr_models.py
