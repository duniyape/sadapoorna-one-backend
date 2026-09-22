import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import counters_collection, delivery_manifests_collection
from datetime import datetime

# Find max manifest_no in delivery_manifests
latest_manifest = delivery_manifests_collection.find_one(
    {},
    sort=[("manifest_no", -1)]
)

print("Latest manifest in DB:", latest_manifest.get("manifest_no") if latest_manifest else None)

# Find the counter for the current month
now = datetime.utcnow()
year = now.strftime("%Y")
month = now.strftime("%m")
counter_id = f"manifest_no:{year}:{month}"

counter = counters_collection.find_one({"_id": counter_id})
print("Current counter in DB:", counter.get("seq") if counter else None)

if latest_manifest and latest_manifest.get("manifest_no"):
    max_seq_str = latest_manifest.get("manifest_no").split("-")[-1]
    if max_seq_str.isdigit():
        max_seq = int(max_seq_str)
        curr_seq = counter.get("seq") if counter else 0
        if max_seq > curr_seq:
            print(f"Fixing counter from {curr_seq} to {max_seq}...")
            counters_collection.update_one(
                {"_id": counter_id},
                {"$set": {"seq": max_seq}}
            )
            print("Fixed!")
        else:
            print("Counter is already correct or ahead.")
