import sqlite3
import pandas as pd
import requests
import json
import sys

def get_sequence():
    conn = sqlite3.connect('../backend/database.sqlite')
    query = """
    SELECT timestamp, voltage, current, power, energy, frequency, power_factor, interval_energy_kwh
    FROM telemetry
    WHERE pzem_id = 'PZEM-001'
    ORDER BY timestamp DESC
    LIMIT 60
    """
    df = pd.read_sql(query, conn)
    df = df.sort_values('timestamp')

    if len(df) == 0:
        print("No data in DB")
        sys.exit(1)

    readings = df.to_dict('records')

    # Pad to 60 if needed
    while len(readings) < 60:
        readings.insert(0, readings[0])

    payload = {
        "appliance_id": "PZEM-001",
        "recent_readings": readings
    }

    res = requests.post("http://localhost:8000/predict", json=payload)
    print("STATUS:", res.status_code)
    print(json.dumps(res.json(), indent=2))

if __name__ == "__main__":
    get_sequence()
