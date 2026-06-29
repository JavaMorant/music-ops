"""Stage 3 — classify each (deduped, identified) track into one of the genre buckets.

Tiered to keep the spend modest:
  * Pass 1 — a cheap pass (Sonnet, low effort) over every uncached track.
  * Pass 2 — only the LOW-confidence results are re-run at higher effort.
Most tracks are settled cheaply; only the hard minority cost more.

Identity-aware: callers pass the real artist/title from the identity stage, so the
model classifies "Drake - Headlines" rather than a junk filename. Results are
cached by a stable per-track key (MBID or normalised artist+title), so re-runs —
including a re-run after an AcoustID key is added and identities improve — only
pay for tracks not already settled.

The 0-5 star "Low / Unrated" split is NOT a genre; it is applied at the output
stage from ratings. This stage only assigns a musical bucket.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

MODEL = "claude-sonnet-4-6"  # project spec default

TAXONOMY = [
    ("Afrobeats", "mainstream afrobeats / afro-pop (Wizkid, Burna Boy, Rema, Asake)"),
    ("Afroswing", "UK afroswing / afrobashment / UK afro-rap (NSG, Not3s, J Hus)"),
    ("Amapiano", "amapiano (log-drum, SA piano)"),
    ("Afrohouse", "afro house / afro tech / 3-step (not amapiano)"),
    ("Disco House", "disco-sampling / nu-disco house"),
    ("Jazz House", "jazzy / soulful house"),
    ("Latin House", "latin / afro-latin house, guaracha, tribal"),
    ("General House", "house that isn't disco/afro/jazz/latin (tech, deep, classic)"),
    ("Garage", "UK garage, 2-step, speed garage, bassline"),
    ("Jungle", "jungle / ragga-jungle"),
    ("DnB", "drum & bass"),
    ("Baile Funk", "brazilian funk / baile funk / funk-phonk"),
    ("Jersey Club", "jersey club / club bounce"),
    ("Latin", "latin / reggaeton / dembow / latin-pop NOT on a house beat"),
    ("Pop", "pop / dance-pop / mainstream pop"),
    ("Throwbacks", "nostalgic singalong crowd-pleasers — 90s/2000s party rap, R&B, pop, disco/funk classics"),
    ("UK Rap", "UK rap / drill / grime"),
    ("US Rap", "US hip-hop / trap, any era"),
    ("RnB", "R&B / soul, any era"),
    ("Afro edits", "edits/bootlegs whose identity IS the afro/amapiano edit"),
    ("RnB edits", "edits/bootlegs whose identity IS the R&B edit"),
    ("Other edits", "edits/bootlegs that are NOT afro or R&B"),
    ("Hype mosh pits", "high-energy rage/mosh — hard 808 rage, hyphy, jump-up moshers"),
]
GENRES = [g for g, _ in TAXONOMY]


def build_system() -> str:
    guide = "\n".join(f"  - {g}: {d}" for g, d in TAXONOMY)
    return (
        "You file a DJ's tracks into exactly ONE genre folder from the allowed list — the "
        "single best home for each track. Allowed genres:\n" + guide + "\n\n"
        "Pick the single best fit. For an edit/bootleg/mashup use an 'edits' bucket ONLY when the "
        "edit is the track's identity; a normal official remix files under its sound. Prefer "
        "'Throwbacks' for clear nostalgic singalong crowd-pleasers. Never invent a genre outside "
        "the list. Never output key or BPM. Give each track a confidence (high/medium/low) and a "
        "short note. Return exactly one entry per track, keyed by the index you were given."
    )


SCHEMA = {
    "type": "object",
    "properties": {"tracks": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "index": {"type": "integer"},
            "genre": {"type": "string", "enum": GENRES},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "note": {"type": "string"},
        },
        "required": ["index", "genre", "confidence", "note"],
        "additionalProperties": False,
    }}},
    "required": ["tracks"],
    "additionalProperties": False,
}


@dataclass
class ClassifyResult:
    genre: str
    confidence: str
    note: str = ""


def track_key(artist: str, title: str, mbid: str | None = None) -> str:
    if mbid:
        return f"mbid:{mbid}"
    return "at:" + re.sub(r"[^a-z0-9]", "", f"{artist}{title}".lower())


def _user_prompt(batch: list[dict]) -> str:
    lines = ["Classify these tracks:"]
    for i, t in enumerate(batch):
        lines += [f"\nTrack {i}:",
                  f"  filename: {t.get('filename', '?')}",
                  f"  artist:   {t.get('artist') or '(none)'}",
                  f"  title:    {t.get('title') or '(none)'}",
                  f"  genre:    {t.get('genre') or '(none)'}"]
    return "\n".join(lines)


def call_anthropic(batch: list[dict], model: str, effort: str) -> list[dict]:
    """Real AI call for one batch → list of {index, genre, confidence, note}."""
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model, max_tokens=8000, thinking={"type": "disabled"},
        system=build_system(),
        messages=[{"role": "user", "content": _user_prompt(batch)}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return json.loads(text)["tracks"]


class ClassifyCache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except ValueError:
                self._data = {}

    def get(self, key: str) -> ClassifyResult | None:
        d = self._data.get(key)
        return ClassifyResult(**d) if d else None

    def put(self, key: str, r: ClassifyResult) -> None:
        self._data[key] = asdict(r)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=1))


def _run_pass(tracks, model, effort, call, batch_size, workers=6):
    """Classify a list of track dicts (each carrying 'key'); return {key: ClassifyResult}.
    Batches run concurrently (the call creates its own client and is thread-safe)."""
    import concurrent.futures as cf
    import threading

    out: dict[str, ClassifyResult] = {}
    lock = threading.Lock()
    chunks = [tracks[i:i + batch_size] for i in range(0, len(tracks), batch_size)]

    def work(chunk):
        try:
            results = call(chunk, model, effort)
        except Exception:
            return  # a failed batch is skipped, not fatal; a re-run picks it up
        with lock:
            for r in results:
                idx = int(r["index"])
                if 0 <= idx < len(chunk):
                    out[chunk[idx]["key"]] = ClassifyResult(
                        r["genre"], r["confidence"], r.get("note", ""))

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, chunks))
    return out


def classify(
    tracks: list[dict],
    *,
    cache: ClassifyCache | None = None,
    call=call_anthropic,
    cheap=(MODEL, "low"),
    strong=(MODEL, "high"),
    batch_size: int = 60,
) -> dict[str, ClassifyResult]:
    """Two-pass tiered classification. ``tracks`` carry key/artist/title/filename/genre.
    Returns {key: ClassifyResult}. ``call`` is injectable for tests."""
    results: dict[str, ClassifyResult] = {}
    todo = []
    for t in tracks:
        if cache is not None:
            hit = cache.get(t["key"])
            if hit is not None:
                results[t["key"]] = hit
                continue
        todo.append(t)

    pass1 = _run_pass(todo, cheap[0], cheap[1], call, batch_size)
    results.update(pass1)

    hard = [t for t in todo if pass1.get(t["key"]) and pass1[t["key"]].confidence == "low"]
    if hard:
        pass2 = _run_pass(hard, strong[0], strong[1], call, batch_size)
        results.update(pass2)  # higher-effort verdict wins for the hard ones

    if cache is not None:
        for k, r in results.items():
            cache.put(k, r)
        cache.save()
    return results
