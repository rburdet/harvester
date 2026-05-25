from __future__ import annotations

import json
import re
from dataclasses import dataclass

import requests


@dataclass
class FormField:
    entry_id: str       # e.g. "entry.1234567890"
    label: str
    type: str           # e.g. "SHORT_ANSWER", "PARAGRAPH"


def _to_viewform(url: str) -> str:
    return re.sub(r"/formResponse$", "/viewform", url)


def _to_formresponse(url: str) -> str:
    if url.endswith("/formResponse"):
        return url
    return re.sub(r"/viewform.*$", "/formResponse", url)


def inspect(url: str) -> list[FormField]:
    """Parse a Google Form's viewform page and return its entry.XXX field ids."""
    r = requests.get(_to_viewform(url), timeout=30)
    r.raise_for_status()
    html = r.text
    m = re.search(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\]);\s*</script>", html, re.S)
    if not m:
        raise RuntimeError("could not find FB_PUBLIC_LOAD_DATA_ on page")
    data = json.loads(m.group(1))
    # data[1][1] is the list of form items
    items = data[1][1] or []
    fields: list[FormField] = []
    for item in items:
        label = item[1] if len(item) > 1 else ""
        item_type = item[3] if len(item) > 3 else None
        sub = item[4] if len(item) > 4 else None
        if not sub:
            continue
        for entry in sub:
            entry_id = entry[0]
            fields.append(
                FormField(
                    entry_id=f"entry.{entry_id}",
                    label=str(label) if label else "",
                    type=str(item_type),
                )
            )
    return fields


def submit(url: str, values: dict[str, str]) -> bool:
    """POST to formResponse. values keys are entry.XXX. Returns True on success."""
    r = requests.post(
        _to_formresponse(url),
        data=values,
        headers={"User-Agent": "Mozilla/5.0 harvester"},
        timeout=30,
        allow_redirects=True,
    )
    return r.status_code == 200 and "form has been recorded" in r.text.lower() \
        or "your response has been recorded" in r.text.lower()
