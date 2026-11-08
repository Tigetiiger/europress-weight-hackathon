"""Hydraulic soft-sensor service for the Europress SMART dashboard.

Serves hourly waste-mass estimates (kg, p10-p90) for a fleet of compactors from device
telemetry only (fill estimate, presses, ram pressure, pressure graph, oil temperature,
public weather). The model is the LightGBM bundle trained by ``weight_train`` in the
simulator project. ALL DATA IS SYNTHETIC (physics-based simulator).
"""
