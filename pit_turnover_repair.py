"""Repair the two dated DNS failures using original archives and original scoring.
Reuses all existing raw responses; no substitute data or changed parameters."""
import json,shutil
from pathlib import Path
import pit_turnover_experiment as t
ROOT=Path(__file__).resolve().parent
src=ROOT/"pit-turnover-input"/"2023"
audit=json.loads((src/"audit.json").read_text())
if set(audit["errors"])!={"2023-10-11","2023-11-06"}:raise ValueError("unexpected repair scope")
if audit["verified_days"]!=233 or audit["expected_days"]!=235:raise ValueError("unexpected input coverage")
dst=t.OUT/"2023"
shutil.copytree(src,dst,dirs_exist_ok=True)
t.collect(2023)
a=json.loads((dst/"audit.json").read_text())
if a["errors"] or a["verified_days"]!=235:raise ValueError("repair still incomplete")
for d,h in audit["hashes"].items():
    if a["hashes"][d]!=h:raise ValueError("existing source hash changed")
old={x["signal"]:x for x in json.loads((src/"signals.json").read_text())}
new=json.loads((dst/"signals.json").read_text())
for x in new:
    if not old[x["signal"]]["source_missing"] and x!=old[x["signal"]]:raise ValueError("unchanged signal changed")
for n in ("audit.json","signals.json"):shutil.copy2(dst/n,src/n)
print("TURNOVER_REPAIR_OK",json.dumps({"verified_days":a["verified_days"],"repaired_signals":[x["signal"] for x in new if old[x["signal"]]["source_missing"]]},ensure_ascii=False),flush=True)
t.aggregate()
