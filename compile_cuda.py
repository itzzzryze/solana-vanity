"""Regenerate the bundled CUDA PTX using the CUDA Toolkit's nvcc."""
from gzip import compress
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    with TemporaryDirectory() as folder:
        ptx = Path(folder) / "search.ptx"
        subprocess.run(
            ["nvcc", "--ptx", "-arch=compute_75", "-DCUDA_BUILD", "-x", "cu",
             str(ROOT / "vanity/search.cl"), "-o", str(ptx)],
            check=True,
        )
        (ROOT / "vanity/search.ptx.gz").write_bytes(compress(ptx.read_bytes(), mtime=0))
