import subprocess

# VRCT_codexCLI-sidecar.exe を強制終了
try:
    subprocess.run(
        ["taskkill", "/IM", "VRCT_codexCLI-sidecar.exe", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
except Exception:
    pass