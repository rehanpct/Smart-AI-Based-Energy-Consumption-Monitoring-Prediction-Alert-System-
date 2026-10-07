import pandas as pd
import numpy as np
import os
import joblib
from sklearn.preprocessing import StandardScaler, MinMaxScaler
from config import SEQUENCE_LENGTH, N_FEATURES, FEATURE_COLUMNS, TARGET, MODELS_DIR, SAMPLING_INTERVAL_SECONDS

def preprocess_data(df, is_training=True, scaler_path=None, target_scaler_path=None):
    df = df.copy()
    if 'timestamp' in df.columns:
        if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed', utc=True)
        df = df.sort_values(by=['appliance_id', 'timestamp']).reset_index(drop=True)

    for _, indexes in df.groupby('appliance_id').groups.items():
        df.loc[indexes] = df.loc[indexes].ffill()
    df = df.fillna(0)

    if 'timestamp' in df.columns:
        df['hour'] = df['timestamp'].dt.hour
        df['day'] = df['timestamp'].dt.dayofweek
        df['month'] = df['timestamp'].dt.month

        df['hour_sin'] = np.sin(2 * np.pi * df['hour']/23.0)
        df['hour_cos'] = np.cos(2 * np.pi * df['hour']/23.0)
        df['day_sin'] = np.sin(2 * np.pi * df['day']/6.0)
        df['day_cos'] = np.cos(2 * np.pi * df['day']/6.0)
        df['month_sin'] = np.sin(2 * np.pi * df['month']/12.0)
        df['month_cos'] = np.cos(2 * np.pi * df['month']/12.0)
        df['is_weekend'] = (df['day'] >= 5).astype(int)
    else:
        for col in ['hour_sin', 'hour_cos', 'day_sin', 'day_cos', 'month_sin', 'month_cos', 'is_weekend']:
            df[col] = 0

    processed_dfs = []
    for app_id, group in df.groupby('appliance_id'):
        group = group.copy()
        if 'interval_energy_kwh' not in group.columns:
            group['interval_energy_kwh'] = group['energy'].diff()
        # Resets/baselines are excluded from training targets, never treated as negative use.
        group['energy_interval'] = pd.to_numeric(group['interval_energy_kwh'], errors='coerce')
        group.loc[group['energy_interval'] < 0, 'energy_interval'] = np.nan
        group['energy_interval'] = group['energy_interval'].fillna(0)

        group['energy_lag_1'] = group['energy_interval'].shift(1).fillna(0)
        group['energy_lag_2'] = group['energy_interval'].shift(2).fillna(0)
        group['energy_lag_3'] = group['energy_interval'].shift(3).fillna(0)

        group['power_lag_1'] = group['power'].shift(1).fillna(0)
        group['power_lag_2'] = group['power'].shift(2).fillna(0)
        group['power_lag_3'] = group['power'].shift(3).fillna(0)

        processed_dfs.append(group)

    df = pd.concat(processed_dfs).sort_index()

    features_df = df[FEATURE_COLUMNS]
    target_series = df[['energy_interval']]

    if scaler_path is None:
        scaler_path = os.path.join(MODELS_DIR, 'scaler.save')
    if target_scaler_path is None:
        target_scaler_path = os.path.join(MODELS_DIR, 'target_scaler.save')

    if is_training:
        scaler = StandardScaler()
        target_scaler = MinMaxScaler(feature_range=(0, 1))

        scaled_features = scaler.fit_transform(features_df)
        scaled_target = target_scaler.fit_transform(target_series)

        joblib.dump(scaler, scaler_path)
        joblib.dump(target_scaler, target_scaler_path)
    else:
        scaler = joblib.load(scaler_path)
        target_scaler = joblib.load(target_scaler_path)

        scaled_features = scaler.transform(features_df)
        scaled_target = target_scaler.transform(target_series)

    return scaled_features, scaled_target.flatten(), df

def create_sequences(features, target, seq_length=SEQUENCE_LENGTH):
    X, y = [], []
    for i in range(len(features) - seq_length):
        X.append(features[i:(i + seq_length)])
        y.append(target[i + seq_length])
    return np.array(X), np.array(y)

def prepare_datasets(csv_path):
    df = pd.read_csv(csv_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed', utc=True)
    df = df.sort_values(by=['appliance_id', 'timestamp'])


    for appliance_id, group in df.groupby('appliance_id'):
        gaps = group['timestamp'].diff().dt.total_seconds().dropna()
        # if len(gaps) and ((gaps < SAMPLING_INTERVAL_SECONDS * 0.5) | (gaps > SAMPLING_INTERVAL_SECONDS * 1.5)).any():
        #    raise ValueError(f'Appliance {appliance_id} contains telemetry outside the configured sampling cadence')

    train_dfs, val_dfs, test_dfs = [], [], []
    for _, group in df.groupby('appliance_id'):
        n = len(group)
        train_idx = int(n * 0.7)
        val_idx = int(n * 0.85)

        train_dfs.append(group.iloc[:train_idx])
        val_dfs.append(group.iloc[train_idx:val_idx])
        test_dfs.append(group.iloc[val_idx:])

    train_df = pd.concat(train_dfs)
    val_df = pd.concat(val_dfs)
    test_df = pd.concat(test_dfs)

    if 'interval_energy_kwh' not in df.columns:
        raise ValueError('Dataset must contain canonical interval_energy_kwh targets')
    os.makedirs(MODELS_DIR, exist_ok=True)
    X_train_raw, y_train_raw, _ = preprocess_data(train_df, is_training=True)
    X_val_raw, y_val_raw, _ = preprocess_data(val_df, is_training=False)
    X_test_raw, y_test_raw, _ = preprocess_data(test_df, is_training=False)

    X_train, y_train = create_sequences(X_train_raw, y_train_raw)
    X_val, y_val = create_sequences(X_val_raw, y_val_raw)
    X_test, y_test = create_sequences(X_test_raw, y_test_raw)

    return (X_train, y_train), (X_val, y_val), (X_test, y_test)
