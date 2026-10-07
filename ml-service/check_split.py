import os
import pandas as pd
from config import DATASETS_DIR
from data_pipeline import prepare_datasets

csv_path = os.path.join(DATASETS_DIR, 'canonical_simulation_telemetry.csv')
df = pd.read_csv(csv_path)
df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed', utc=True)
df = df.sort_values(by=['appliance_id', 'timestamp'])

print("Total samples:", len(df))

train_dfs, val_dfs, test_dfs = [], [], []
for app, group in df.groupby('appliance_id'):
    n = len(group)
    train_idx = int(n * 0.7)
    val_idx = int(n * 0.85)

    train_dfs.append(group.iloc[:train_idx])
    val_dfs.append(group.iloc[train_idx:val_idx])
    test_dfs.append(group.iloc[val_idx:])

train_df = pd.concat(train_dfs)
val_df = pd.concat(val_dfs)
test_df = pd.concat(test_dfs)

print("Train split:", len(train_df))
print("Val split:", len(val_df))
print("Test split:", len(test_df))

print(f"Train first: {train_df['timestamp'].min()}, Train last: {train_df['timestamp'].max()}")
print(f"Val first: {val_df['timestamp'].min()}, Val last: {val_df['timestamp'].max()}")
print(f"Test first: {test_df['timestamp'].min()}, Test last: {test_df['timestamp'].max()}")

train_max = train_df['timestamp'].max()
val_min = val_df['timestamp'].min()
test_min = test_df['timestamp'].min()
print(f"Leakage Train->Val: {train_max >= val_min}")
print(f"Leakage Val->Test: {val_df['timestamp'].max() >= test_min}")
