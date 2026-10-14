import asyncio
import csv
import json
from pathlib import Path


class Trail:
    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()

    async def append(self, record_id: str, data: dict):
        async with self.lock:
            records = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
            if any(r["record_id"] == record_id for r in records):
                return
            records.append({"record_id": record_id, **data})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(records, indent=2), encoding="utf-8")
            temp.replace(self.path)


def export_posts(run_dir: Path, accepted: list[dict]):
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "posts.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["Post Text", "Image"])
        writer.writeheader()
        for post in accepted:
            image = Path(post["image"])
            if image.is_absolute() or ".." in image.parts:
                raise ValueError("image must be relative to run folder")
            writer.writerow({"Post Text": "\n".join(post["tweets"]), "Image": image.as_posix()})
    (run_dir / "posts.json").write_text(json.dumps(accepted, ensure_ascii=False, indent=2), encoding="utf-8")
