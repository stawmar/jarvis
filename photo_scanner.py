from pathlib import Path
from PIL import Image

PHOTOS_DIR = Path(
    "/srv/data/nextcloud/data/marek/files/"
    "Raporty/JARVIS_TEST/Photos"
)

EXTENSIONS = {".jpg", ".jpeg", ".png"}


def scan_photos(directory):
    photos = []

    files = sorted(
        (
            path for path in directory.iterdir()
            if path.is_file()
            and path.suffix.lower() in EXTENSIONS
        ),
        key=lambda path: path.name
    )

    for index, path in enumerate(files, start=1):
        try:
            with Image.open(path) as image:
                width, height = image.size

            photos.append({
                "index": index,
                "filename": path.name,
                "width": width,
                "height": height,
                "status": "OK"
            })

        except Exception as exc:
            photos.append({
                "index": index,
                "filename": path.name,
                "status": "ERROR",
                "error": str(exc)
            })

    return photos


if __name__ == "__main__":
    photos = scan_photos(PHOTOS_DIR)

    for photo in photos:
        print(
            f"{photo['index']:03d} | "
            f"{photo['filename']} | "
            f"{photo['status']}"
        )

    print(f"\nŁącznie zdjęć: {len(photos)}")
    print(
        "Błędy odczytu:",
        sum(p["status"] == "ERROR" for p in photos)
    )