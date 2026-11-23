"""Download the CASE dataset (Sharma et al. 2019) into datasets/raw/case.

Run from the repository root:
    python -m sw.classification.datasets.download_case

Source: Karan Sharma, Christian Nordsletten, Maarten De Vos (2019), CASE 2019:
The Multimodal DataSet for Emotion Analysis in Physical and Virtual Reality
Environments, figshare article 8869157, doi:10.6084/m9.figshare.8869157.
Only the full dataset (CASE_full.zip, ~4.55 GB) is downloaded; it contains the
interpolated physiological signals (including GSR) and the continuous
valence/arousal annotations of all 30 participants.

The download is idempotent: an already-extracted dataset is left untouched, an
already-downloaded archive is only unzipped, and the archive is removed after
a successful extraction (pass --keep-zip to keep it).
"""

import argparse
import zipfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
DOWNLOAD_URL = "https://ndownloader.figshare.com/files/16260497"
SUBJECTS = tuple(f"sub_{number:02d}" for number in range(1, 31))
CITATION = ("Sharma, K., Nordsletten, C., & De Vos, M. (2019). CASE 2019: The "
            "Multimodal DataSet for Emotion Analysis in Physical and Virtual "
            "Reality Environments. Figshare. https://doi.org/10.6084/m9.figshare.8869157")


def extracted_complete(root: Path):
    """True when the extracted tree contains all 30 physiological/annotation files."""
    physiological = root / "extracted/data/interpolated/physiological"
    annotations = root / "extracted/data/interpolated/annotations"
    return all((physiological / f"{subject}.csv").exists() and
               (annotations / f"{subject}.csv").exists() for subject in SUBJECTS)


def _download(archive: Path):
    temporary = archive.with_suffix(".zip.part")
    with urlopen(DOWNLOAD_URL, timeout=90) as response, temporary.open("wb") as target:
        while chunk := response.read(1 << 20):
            target.write(chunk)
    temporary.replace(archive)


def download_dataset(root: Path = ROOT, keep_zip: bool = False):
    """Download, verify by member count, and extract CASE under datasets/raw/case."""
    raw = root / "raw/case"
    raw.mkdir(parents=True, exist_ok=True)
    if extracted_complete(root):
        print(f"CASE already extracted in {raw / 'extracted'}; nothing to do.")
        return
    archive = raw / "CASE_full.zip"
    if not archive.exists():
        print(f"Downloading {DOWNLOAD_URL} (~4.55 GB) to {archive} ...")
        _download(archive)
    print(f"Extracting {archive} ...")
    with zipfile.ZipFile(archive) as bundled:
        bundled.extractall(raw / "extracted")
    if not extracted_complete(root):
        raise ValueError("Extraction incomplete: missing subject CSV files.")
    if not keep_zip:
        archive.unlink()
    print(f"Extracted {len(SUBJECTS)} subjects in {raw / 'extracted'}")
    print(CITATION)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-zip", action="store_true",
                        help="keep CASE_full.zip after extraction")
    download_dataset(keep_zip=parser.parse_args().keep_zip)
