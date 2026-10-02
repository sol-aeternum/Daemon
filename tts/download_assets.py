"""Build-only asset acquisition. Runtime never imports or invokes this module."""

import hashlib
from pathlib import Path
import urllib.request

ASSETS = {
    "kokoro-v1.0.onnx": "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"


def download(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, expected in ASSETS.items():
        digest = hashlib.sha256()
        # URLs are fixed public build inputs, never request-supplied.
        with urllib.request.urlopen(BASE + name, timeout=120) as response:
            with (root / name).open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError("TTS asset checksum mismatch")
    with urllib.request.urlopen(
        "https://www.apache.org/licenses/LICENSE-2.0.txt", timeout=30
    ) as response:
        license_text = response.read(32768)
    if (
        hashlib.sha256(license_text).hexdigest()
        != "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
    ):
        raise RuntimeError("Model license checksum mismatch")
    (root / "LICENSE-2.0.txt").write_bytes(license_text)


if __name__ == "__main__":
    download(Path("/opt/models"))
