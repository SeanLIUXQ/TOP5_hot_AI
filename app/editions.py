"""Public daily editions and the original Monday-keyed weekly exports."""

import json
import re
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.core.dates import current_week_start

DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def dated_files(folder: Path, prefix: str = "") -> dict[str, Path]:
    paths = {}
    for path in folder.glob(f"{prefix}*.json"):
        key = path.stem.removeprefix(prefix)
        if DAY.fullmatch(key):
            date.fromisoformat(key)
            paths[key] = path
    return paths


def read_payload(path: Path) -> dict:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        temporary = Path(tmp.name)
        try:
            json.dump(value, tmp, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            tmp.write("\n")
        except BaseException:
            tmp.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class EditionArchive:
    def __init__(self, root: Path):
        self.root = root
        self.weeks = dated_files(root, "rankings-")
        daily = dated_files(root / "editions")
        # A rolling weekly export for a week first observed on (say) Wednesday
        # must not appear as a fictitious Monday edition.
        weeks_with_editions = {
            current_week_start(date.fromisoformat(key)).isoformat() for key in daily
        }
        self.editions = {
            k: p for k, p in self.weeks.items() if k not in weeks_with_editions
        } | daily
        self.infra = dated_files(root / "infra")

    def edition(self, key: str) -> dict | None:
        path = self.editions.get(key)
        if path is None:
            return None
        payload = read_payload(path)
        return {"date": key, "kind": "weekly", **payload}

    def infra_report(self, key: str | None = None) -> dict | None:
        path = self.infra.get(key or max(self.infra, default=""))
        return read_payload(path) if path else None

    def publish(self, observed: date, ranking: dict, infra: dict, markdown: str, csv: str) -> None:
        key = observed.isoformat()
        week = current_week_start(observed).isoformat()
        if (
            ranking["week_start"] != week
            or infra["date"] != key
            or not ranking["items"]
            or not infra["items"]
        ):
            raise ValueError("Missing or mismatched ranking and AI Infra editions")
        # During the upgrade, preserve an existing Monday's original payload
        # before replacing its rolling weekly alias with Wednesday's observations.
        old_week = self.weeks.get(week)
        if old_week and self.editions.get(week) == old_week and key != week:
            write_json(self.root / "editions" / f"{week}.json", self.edition(week))
        daily = {**ranking, "date": key, "kind": "daily"}
        write_json(self.root / "editions" / f"{key}.json", daily)
        write_json(self.root / "infra" / f"{key}.json", infra)
        write_json(self.root / f"rankings-{week}.json", daily)
        for extension, report in (("md", markdown), ("csv", csv)):
            (self.root / f"weekly-{week}.{extension}").write_text(report, encoding="utf-8")
        updated = EditionArchive(self.root)
        write_json(self.root / "rankings-latest.json", updated.edition(max(updated.editions)))
        write_json(
            self.root / "ranking-weeks.json",
            {
                "score_version": ranking["score_version"],
                "weeks": sorted(updated.weeks, reverse=True),
            },
        )
        write_json(self.root / "editions.json", {"dates": sorted(updated.editions, reverse=True)})
        write_json(self.root / "infra-dates.json", {"dates": sorted(updated.infra, reverse=True)})
