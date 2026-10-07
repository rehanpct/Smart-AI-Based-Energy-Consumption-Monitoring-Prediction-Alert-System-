from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
import os
import joblib
import json
import pandas as pd
import numpy as np

from config import SEQUENCE_LENGTH, MODELS_DIR, FEATURE_COLUMNS, MODEL_VERSION, SAMPLING_INTERVAL_SECONDS, SAMPLING_INTERVAL_MS, FORECAST_HORIZON_INTERVALS, TARGET
from simulation import generate_dataset
from lstm_model import train_model
from anomaly import detect_anomalies
from data_pipeline import preprocess_data

import tensorflow as tf

app = FastAPI(title="Smart Energy Monitor ML Service")

class TelemetryReading(BaseModel):
    timestamp: str
    voltage: float = Field(ge=0, le=300)
    current: float = Field(ge=0, le=250)
    power: float = Field(ge=0, le=50000)
    energy: float = Field(ge=0)
    interval_energy_kwh: Optional[float] = Field(default=None, ge=0)
    frequency: float = Field(ge=45, le=65)
    power_factor: float = Field(ge=0, le=1)

class PredictRequest(BaseModel):
    appliance_id: str
    recent_readings: List[TelemetryReading]

class AnomalyRequest(BaseModel):
    readings: List[dict]

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.post("/dataset/generate")
def generate_data(background_tasks: BackgroundTasks):
    background_tasks.add_task(generate_dataset)
    return {"message": "Dataset generation started in background."}

@app.post("/train")
def train(background_tasks: BackgroundTasks):
    background_tasks.add_task(train_model)
    return {"message": "Model training started in background."}

@app.post("/predict")
def predict(req: PredictRequest):
    if len(req.recent_readings) != SEQUENCE_LENGTH:
        raise HTTPException(400, f"Expected exactly {SEQUENCE_LENGTH} readings.")
    timestamps = pd.to_datetime([r.timestamp for r in req.recent_readings], utc=True, errors='coerce')
    if timestamps.isna().any() or not timestamps.is_monotonic_increasing:
        raise HTTPException(400, "Readings must have valid, strictly ordered timestamps.")

    if len(timestamps) > 1:
        gaps = timestamps.to_series().diff().dt.total_seconds().dropna()
        if (gaps < SAMPLING_INTERVAL_SECONDS * 0.1).any() or (gaps > SAMPLING_INTERVAL_SECONDS * 3.0).any():
            raise HTTPException(400, "Readings do not match the configured sampling cadence.")
    if any(r.interval_energy_kwh is None for r in req.recent_readings):
        raise HTTPException(400, "Each reading must include interval_energy_kwh.")

    df = pd.DataFrame([r.dict() for r in req.recent_readings])
    df['appliance_id'] = req.appliance_id

    try:
        scaled_features, _, _ = preprocess_data(df, is_training=False)
        X = np.expand_dims(scaled_features[-SEQUENCE_LENGTH:], axis=0)

        metadata_path = os.path.join(MODELS_DIR, 'metadata.json')
        if not os.path.exists(metadata_path):
            raise HTTPException(404, "Compatible model metadata not found; train the model first.")
        with open(metadata_path, 'r') as metadata_file:
            metadata = json.load(metadata_file)
        if metadata.get('model_version') != MODEL_VERSION or metadata.get('feature_order') != FEATURE_COLUMNS or metadata.get('sequence_length') != SEQUENCE_LENGTH:
            raise HTTPException(409, "Model metadata is incompatible with this service configuration.")
        model_path = os.path.join(MODELS_DIR, 'lstm_model.keras')
        if not os.path.exists(model_path):
            raise HTTPException(404, "Model not found.")

        model = tf.keras.models.load_model(model_path)
        if tuple(model.input_shape[1:]) != (SEQUENCE_LENGTH, len(FEATURE_COLUMNS)) or model.output_shape[-1] != 1:
            raise HTTPException(409, "Model input/output shape does not match the canonical feature contract.")
        pred_scaled = model.predict(X)[0][0]

        target_scaler_path = os.path.join(MODELS_DIR, 'target_scaler.save')
        target_scaler = joblib.load(target_scaler_path)
        pred_unscaled = target_scaler.inverse_transform([[pred_scaled]])[0][0]
        pred_unscaled = float(np.clip(pred_unscaled, a_min=0, a_max=None))

        return {
            "source": metadata['source'],
            "model": "energy_lstm",
            "model_version": metadata['model_version'],
            "appliance_id": req.appliance_id,
            "prediction": pred_unscaled,
            "unit": "kWh",
            "target": TARGET,
            "forecast_horizon_intervals": FORECAST_HORIZON_INTERVALS,
            "forecast_horizon_seconds": SAMPLING_INTERVAL_SECONDS * FORECAST_HORIZON_INTERVALS,
            "timestamp": df['timestamp'].iloc[-1]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/evaluate")
def evaluate():
    metrics_path = os.path.join(MODELS_DIR, 'metrics.json')
    if os.path.exists(metrics_path):
        with open(metrics_path, 'r') as f:
            return json.load(f)
    return {"message": "No evaluation metrics found."}

@app.get("/model")
def get_model():
    metadata_path = os.path.join(MODELS_DIR, 'metadata.json')
    model_path = os.path.join(MODELS_DIR, 'lstm_model.keras')
    if not os.path.exists(metadata_path) or not os.path.exists(model_path):
        return {"name": "LSTM Energy Forecaster", "version": None, "status": "not_trained", "dataset_source": "simulation", "features": FEATURE_COLUMNS, "sequence_length": SEQUENCE_LENGTH, "sampling_interval": f"{SAMPLING_INTERVAL_MS}ms", "training_date": None, "appliance_scope": "per-appliance sequence", "metrics": None}
    with open(metadata_path, 'r') as metadata_file:
        metadata = json.load(metadata_file)
    return {"name": "LSTM Energy Forecaster", "version": metadata['model_version'], "status": "trained", "dataset_source": metadata['source'], "features": metadata['feature_order'], "sequence_length": metadata['sequence_length'], "sampling_interval": f"{metadata['sampling_interval_ms']}ms", "forecast_horizon_seconds": metadata['forecast_horizon_seconds'], "target": metadata['target'], "training_date": metadata['training_timestamp'], "appliance_scope": metadata['appliance_scope'], "metrics": metadata.get('metrics')}

@app.get("/metrics")
def get_metrics():
    metrics_path = os.path.join(MODELS_DIR, 'metrics.json')
    if os.path.exists(metrics_path):
        with open(metrics_path, 'r') as f:
            return json.load(f)
    return {"message": "No metrics available."}

@app.get("/models")
def list_models():
    if os.path.exists(MODELS_DIR):
        return os.listdir(MODELS_DIR)
    return []

@app.post("/anomaly/detect")
def detect_anomaly(req: AnomalyRequest):
    anomalies = detect_anomalies(req.readings)
    return {"anomalies": anomalies}
