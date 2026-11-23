"""Download a small, checksum-verified, annotated EDA subset (no account required).

Run from the repository root:
    python -m sw.classification.datasets.download_physionet

Source: Andrea Hongn, Facundo Bosch, Lara Prado and Paula Bonomini (2025),
PhysioNet wearable-device-dataset 1.0.1, doi:10.13026/he0v-tf17.
Only EDA/tags and provenance are downloaded, not the other sensor modalities.
"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
from urllib.request import urlopen

BASE = "https://physionet.org/files/wearable-device-dataset/1.0.1/"
ROOT = Path(__file__).resolve().parent
TRAIN_SUBJECTS = ("f01", "f02", "f03", "f04", "f05", "f06", "f08", "f09")
TEST_SUBJECTS = ("f10", "f11", "f15", "f16")
CITATION = ("Hongn, A., Bosch, F., Prado, L., & Bonomini, P. (2025). "
            "Wearable Device Dataset from Induced Stress and Structured Exercise Sessions "
            "(version 1.0.1). PhysioNet. https://doi.org/10.13026/he0v-tf17")


def _fetch(url):
    for attempt in range(3):
        try:
            with urlopen(url, timeout=90) as response:
                return response.read()
        except OSError:
            if attempt == 2:
                raise


def download_dataset(root: Path = ROOT):
    raw = root / "raw/physionet"
    raw.mkdir(parents=True, exist_ok=True)
    checksums = _fetch(BASE + "SHA256SUMS.txt")
    (raw / "SHA256SUMS.txt").write_bytes(checksums)
    hashes = {line.split(maxsplit=1)[1]: line.split()[0] for line in checksums.decode().splitlines()}

    def download(relative):
        target = raw / relative
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == hashes[relative]:
            return
        content = _fetch(BASE + relative)
        if hashlib.sha256(content).hexdigest() != hashes[relative]:
            raise ValueError(f"Checksum mismatch: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(content)
        temporary.replace(target)

    files = ["LICENSE.txt", "README.txt", "data_constraints.txt", "Stress_Level_v2.csv"]
    files += [f"Wearable_Dataset/STRESS/{subject}/{name}" for subject in TRAIN_SUBJECTS + TEST_SUBJECTS
              for name in ("EDA.csv", "tags.csv")]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, files))

    print(f"Downloaded and verified EDA/tags for {len(TRAIN_SUBJECTS + TEST_SUBJECTS)} subjects in {raw}")
    print(CITATION)


if __name__ == "__main__":
    download_dataset()
