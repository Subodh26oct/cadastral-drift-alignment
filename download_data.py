import os
import urllib.request
from pathlib import Path

VILLAGES = [
    "34855_vadnerbhairav_chandavad_nashik",
    "12429_malatavadi_chandgad_kolhapur"
]

FILES = [
    "input.geojson",
    "imagery.tif",
    "boundaries.tif",
    "example_truths.geojson"
]

BASE_URL = "https://hiring.bhume.in/data"

def download_file(url, dest_path):
    print(f"Downloading {url} to {dest_path}...")
    try:
        urllib.request.urlretrieve(url, dest_path)
        print("Success.")
    except Exception as e:
        print(f"Failed to download {url}: {e}")

def main():
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    
    for village in VILLAGES:
        village_dir = data_dir / village
        village_dir.mkdir(exist_ok=True)
        
        for file in FILES:
            url = f"{BASE_URL}/{village}/{file}"
            dest = village_dir / file
            if dest.exists():
                print(f"{dest} already exists, skipping.")
                continue
            download_file(url, dest)

if __name__ == "__main__":
    main()
