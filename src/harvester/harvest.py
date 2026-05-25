from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

import requests


API = "https://api.harvestapp.com/api/v2"


@dataclass
class HarvestClient:
    account_id: str
    access_token: str
    user_agent: str

    @classmethod
    def from_env(cls) -> "HarvestClient":
        try:
            return cls(
                account_id=os.environ["HARVEST_ACCOUNT_ID"],
                access_token=os.environ["HARVEST_ACCESS_TOKEN"],
                user_agent=os.environ.get(
                    "HARVEST_USER_AGENT", "harvester (unknown)"
                ),
            )
        except KeyError as e:
            raise SystemExit(f"missing env var: {e.args[0]}")

    def _headers(self) -> dict:
        return {
            "Harvest-Account-ID": self.account_id,
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": self.user_agent,
        }

    def _get_paginated(self, path: str, params: dict | None = None, key: str | None = None) -> list[dict]:
        url = f"{API}/{path}"
        out: list[dict] = []
        params = dict(params or {})
        params.setdefault("per_page", 100)
        while url:
            r = requests.get(url, headers=self._headers(), params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            page_key = key or path.split("/")[-1].split("?")[0]
            out.extend(data.get(page_key, []))
            url = (data.get("links") or {}).get("next")
            params = {}  # next URL already has params
        return out

    def me(self) -> dict:
        r = requests.get(f"{API}/users/me.json", headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    def company(self) -> dict:
        r = requests.get(f"{API}/company.json", headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    def project_assignments(self) -> list[dict]:
        return self._get_paginated("users/me/project_assignments", key="project_assignments")

    def existing_entries(
        self, *, project_id: int, frm: date, to: date, user_id: int
    ) -> list[dict]:
        return self._get_paginated(
            "time_entries",
            params={
                "user_id": user_id,
                "project_id": project_id,
                "from": frm.isoformat(),
                "to": to.isoformat(),
            },
            key="time_entries",
        )

    def create_time_entry(
        self,
        *,
        project_id: int,
        task_id: int,
        spent_date: date,
        hours: float,
        notes: str,
    ) -> dict:
        payload = {
            "project_id": project_id,
            "task_id": task_id,
            "spent_date": spent_date.isoformat(),
            "hours": round(hours, 2),
            "notes": notes,
        }
        r = requests.post(
            f"{API}/time_entries",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"harvest {r.status_code}: {r.text}")
        return r.json()
