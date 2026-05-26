from __future__ import annotations

import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import click
import yaml
from dotenv import load_dotenv
from rich.console import Console

from . import aggregate as agg
from . import commits as commits_mod
from . import invoice as invoice_mod
from . import pdf as pdf_mod
from . import review
from . import spreadsheet
from .harvest import HarvestClient
from .pdf import ReportMeta


ROOT = Path.cwd()


def _load_config(path: Path) -> dict:
    if not path.exists():
        raise click.ClickException(f"config not found: {path} — copy config.example.yaml")
    return yaml.safe_load(path.read_text())


def _parse_month(s: str) -> tuple[int, int, str]:
    y, m = s.split("-")
    return int(y), int(m), s


@click.group()
def cli():
    """Auto-fill Harvest timesheets from git commits."""
    load_dotenv(ROOT / ".env")


@cli.command("list-projects")
def list_projects_cmd():
    client = HarvestClient.from_env()
    for pa in client.project_assignments():
        p = pa["project"]
        c = pa["client"]
        print(f"{p['id']}  {c['name']} / {p['name']}")
        for ta in pa["task_assignments"]:
            t = ta["task"]
            print(f"    task {t['id']}: {t['name']}")


@cli.command("delete-entries")
@click.option("--from", "from_date", required=True, help="YYYY-MM-DD")
@click.option("--to", "to_date", required=True, help="YYYY-MM-DD")
@click.option("--project-id", required=True, type=int)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def delete_entries_cmd(from_date: str, to_date: str, project_id: int, yes: bool):
    """Delete your time entries in [from..to] on the given project. Useful for cleaning up test runs."""
    from datetime import date as _date
    import requests as _r
    client = HarvestClient.from_env()
    me = client.me()
    entries = client.existing_entries(
        project_id=project_id,
        frm=_date.fromisoformat(from_date),
        to=_date.fromisoformat(to_date),
        user_id=me["id"],
    )
    if not entries:
        print("nothing to delete")
        return
    print(f"will delete {len(entries)} entries on project {project_id}:")
    for e in entries:
        print(f"  id={e['id']}  {e['spent_date']}  {e['hours']}h  {e.get('notes','')[:60]!r}")
    if not yes:
        if input("proceed? [y/N] ").strip().lower() != "y":
            print("aborted")
            return
    for e in entries:
        url = f"https://api.harvestapp.com/api/v2/time_entries/{e['id']}"
        r = _r.delete(url, headers=client._headers(), timeout=30)
        if r.status_code in (200, 204):
            print(f"  deleted {e['id']}")
        else:
            print(f"  FAILED {e['id']}: {r.status_code} {r.text}")


def _build_invoice_meta(
    inv_cfg: dict, year: int, mon: int, *, number_override: int | None = None
) -> invoice_mod.InvoiceMeta:
    """Build an InvoiceMeta for `year-mon` from the `invoice:` config block.

    Number auto-increments by 1 per month from `latest_invoice_number` (anchored
    at `latest_invoice_month`). Date = last day of `year-mon`.
    """
    from calendar import monthrange
    from datetime import date as _date

    last_day = monthrange(year, mon)[1]
    invoice_date = _date(year, mon, last_day)

    if number_override is not None:
        number: int = number_override
    else:
        try:
            anchor_num = int(inv_cfg["latest_invoice_number"])
            anchor_month = str(inv_cfg["latest_invoice_month"])
            ay, am = (int(p) for p in anchor_month.split("-"))
        except (KeyError, ValueError) as e:
            raise click.ClickException(
                "invoice.latest_invoice_number and invoice.latest_invoice_month "
                f"(YYYY-MM) are required to auto-number invoices ({e})"
            )
        offset = (year - ay) * 12 + (mon - am)
        if offset < 0:
            raise click.ClickException(
                f"month {year:04d}-{mon:02d} is before "
                f"invoice.latest_invoice_month {anchor_month}"
            )
        number = anchor_num + offset

    bill = inv_cfg["bill_to"]
    items = [
        invoice_mod.LineItem(
            description=i["description"],
            qty=float(i.get("qty", 1)),
            unit_price=float(i["unit_price"]),
        )
        for i in inv_cfg.get("line_items", [])
    ]
    return invoice_mod.InvoiceMeta(
        vendor_name=inv_cfg["vendor"]["name"],
        vendor_title=inv_cfg["vendor"].get("title", ""),
        number=number,
        invoice_date=invoice_date,
        bill_to=invoice_mod.BillTo(
            name=bill["name"],
            address_lines=list(bill.get("address_lines", [])),
            email=bill.get("email", ""),
        ),
        items=items,
        currency=inv_cfg.get("currency", "$"),
    )


@cli.command("invoice")
@click.option("--month", required=True, help="YYYY-MM — invoice covers this month")
@click.option("--number", type=int, default=None,
              help="Override the auto-computed invoice number.")
@click.option("--config", "config_path", default="config.yaml", show_default=True)
def invoice_cmd(month: str, number: int | None, config_path: str):
    """Render an invoice PDF for the given month (out/invoice_<number>.pdf).

    Invoice number auto-increments by 1 per month from
    `invoice.latest_invoice_number` (anchored at `invoice.latest_invoice_month`).
    Invoice date is set to the last day of the target month.
    """
    cfg = _load_config(ROOT / config_path)
    inv = cfg.get("invoice")
    if not inv:
        raise click.ClickException("config.yaml missing an 'invoice:' section")
    year, mon = (int(p) for p in month.split("-"))
    meta = _build_invoice_meta(inv, year, mon, number_override=number)
    out = invoice_mod.write_invoice(meta, ROOT / "out")
    print(f"wrote {out}  (#{meta.number}, dated {meta.invoice_date.isoformat()})")


@cli.command("run")
@click.option("--month", required=True, help="YYYY-MM")
@click.option("--config", "config_path", default="config.yaml", show_default=True)
@click.option("--dry-run", is_flag=True, help="Show table, write nothing.")
@click.option("--skip-harvest", is_flag=True)
@click.option("--yes", is_flag=True, help="Skip the y/n/edit prompt.")
@click.option("--only-day", "only_day", default=None, help="Limit to a single YYYY-MM-DD entry (testing).")
def run_cmd(
    month: str,
    config_path: str,
    dry_run: bool,
    skip_harvest: bool,
    yes: bool,
    only_day: str | None,
):
    cfg = _load_config(ROOT / config_path)
    year, mon, label = _parse_month(month)
    tz = ZoneInfo(os.environ.get("LOCAL_TZ", "UTC"))
    author = os.environ.get("GIT_AUTHOR_EMAIL")
    if not author:
        raise click.ClickException("GIT_AUTHOR_EMAIL not set in .env")

    console = Console()
    console.print(f"[bold]Fetching commits[/bold] for {label} as {author}…")
    commits = commits_mod.fetch_month(
        cfg["repos"], author, year, mon, tz, console=console
    )
    console.print(
        f"[bold]Found {len(commits)} commit(s)[/bold] "
        f"across {len(cfg['repos'])} repo(s)"
    )

    entries = agg.build_entries(
        commits,
        year,
        mon,
        ticket_patterns=cfg["tickets"]["patterns"],
        min_hours=float(cfg["hours"]["min_per_day"]),
        max_hours=float(cfg["hours"]["max_per_day"]),
        fill=cfg["hours"].get("fill_no_commit_days", "weekdays"),
    )

    if only_day:
        from datetime import date as _date
        target = _date.fromisoformat(only_day)
        entries = [e for e in entries if e.day == target]
        if not entries:
            console.print(f"[yellow]no entry for {only_day}[/yellow]")
            return
        console.print(f"[dim]--only-day: limiting to {only_day}[/dim]")

    if not entries:
        console.print("[yellow]no entries to log[/yellow]")
        return

    review.render(entries, label)

    # Invoice is config-driven (independent of entries), so generate it even on
    # --dry-run. Skip silently if `invoice:` isn't configured.
    if inv_cfg := cfg.get("invoice"):
        inv_meta = _build_invoice_meta(inv_cfg, year, mon)
        inv_path = invoice_mod.write_invoice(inv_meta, ROOT / "out")
        console.print(
            f"  wrote {inv_path}  (#{inv_meta.number}, "
            f"dated {inv_meta.invoice_date.isoformat()})"
        )

    if dry_run:
        console.print("[dim]--dry-run: stopping before any other writes[/dim]")
        return

    entries = review.prompt_confirm(entries, assume_yes=yes)

    # resolve names once so the spreadsheet/PDF can show them
    client = HarvestClient.from_env()
    me = client.me()
    project_id = int(cfg["harvest"]["project_id"])
    task_id = int(cfg["harvest"]["task_id"])
    proj_name, task_name, client_name = _resolve_names(client, project_id, task_id)
    # Wordmark on the Clients Report. Config wins; otherwise read from Harvest.
    account_name = (cfg.get("branding") or {}).get("account_name") or ""
    if not account_name:
        try:
            account_name = (client.company().get("name") or "").strip()
        except Exception:
            account_name = ""
    from calendar import monthrange
    last_day = monthrange(year, mon)[1]
    meta = ReportMeta(
        title="Timesheet",
        brand=client_name or "",
        timeframe=f"{mon:02d}/01/{year} – {mon:02d}/{last_day:02d}/{year}",
        author=f"{me.get('first_name','').strip()} {me.get('last_name','').strip()}".strip()
        or me.get("email", ""),
        project=proj_name or str(project_id),
        task=task_name or str(task_id),
        account=account_name,
    )

    # spreadsheet + pdf
    xlsx, csvp = spreadsheet.write(entries, ROOT / "out", label)
    console.print(f"  wrote {xlsx}")
    console.print(f"  wrote {csvp}")
    pdf_path = pdf_mod.write(entries, ROOT / "out", label, meta=meta)
    console.print(f"  wrote {pdf_path}")
    from datetime import date as _date
    clients_pdf = pdf_mod.write_clients_report(
        entries, ROOT / "out",
        _date(year, mon, 1), _date(year, mon, last_day),
        meta=meta,
    )
    console.print(f"  wrote {clients_pdf}")

    # harvest
    if not skip_harvest:
        from datetime import date as _date
        frm = _date(year, mon, 1)
        to = _date(year, mon, last_day)
        existing = client.existing_entries(
            project_id=project_id, frm=frm, to=to, user_id=me["id"]
        )
        already = {e["spent_date"] for e in existing}
        posted = skipped = 0
        for e in entries:
            if e.day.isoformat() in already:
                console.print(f"  [yellow]skip[/yellow] {e.day} (already logged)")
                skipped += 1
                continue
            try:
                client.create_time_entry(
                    project_id=project_id,
                    task_id=task_id,
                    spent_date=e.day,
                    hours=e.hours,
                    notes=e.notes,
                )
                console.print(f"  [green]posted[/green] {e.day} ({e.hours:.2f}h)")
                posted += 1
            except RuntimeError as exc:
                console.print(f"  [red]failed[/red] {e.day}: {exc}")
        console.print(f"Harvest: {posted} posted, {skipped} skipped")


def _resolve_names(client, project_id: int, task_id: int) -> tuple[str, str, str]:
    """Return (project_name, task_name, client_name) by scanning project_assignments."""
    for pa in client.project_assignments():
        if pa["project"]["id"] == project_id:
            task_name = ""
            for ta in pa["task_assignments"]:
                if ta["task"]["id"] == task_id:
                    task_name = ta["task"]["name"]
                    break
            return pa["project"]["name"], task_name, pa["client"]["name"]
    return "", "", ""


if __name__ == "__main__":
    cli()
