import asyncio
import csv
import json
from pathlib import Path, PureWindowsPath


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
    images=[]
    for post in accepted:
        image=PureWindowsPath(post["image"])
        if image.anchor or ".." in image.parts or not image.parts:
            raise ValueError("image must be relative to run folder")
        relative=Path(*image.parts)
        if not (run_dir/relative).resolve().is_relative_to(run_dir.resolve()):
            raise ValueError("image must be relative to run folder")
        images.append(relative.as_posix())
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "posts.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["Post Text", "Image"])
        writer.writeheader()
        for post,image in zip(accepted,images):
            writer.writerow({"Post Text": "\n".join(post["tweets"]), "Image": image})
    records=[{**post,"image":image} for post,image in zip(accepted,images)]
    (run_dir / "posts.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
