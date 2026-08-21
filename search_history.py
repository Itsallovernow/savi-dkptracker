"""Quick search utility for dkp_history.json"""
import json
import sys

search_term = sys.argv[1].lower() if len(sys.argv) > 1 else "crimson robe"

with open("dkp_history.json", "r", encoding="utf-8") as f:
    data = json.load(f)

matches = [r for r in data["records"] if search_term in r.get("item_name", "").lower()]

if not matches:
    print(f"No matches for '{search_term}' in local history")
else:
    print(f"Found {len(matches)} match(es) for '{search_term}':\n")
    for m in matches:
        print(f"  {m['item_name']} -> {m['winner']} {m['amount']} DKP")
        print(f"    timestamp: {m['timestamp']}")
        print(f"    uploaded_by: {m.get('uploaded_by', '(none)')}")
        print(f"    log_source: {m.get('log_source', '(none)')}")
        print(f"    dedup_key: {m['dedup_key']}")
        print()
