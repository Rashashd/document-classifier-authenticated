import { apiGet, apiPatch } from "./client";
import type { PredictionListResponse, PredictionRead, PredictionUpdate } from "./types";

export async function listRecentPredictions(skip = 0, limit = 50): Promise<PredictionListResponse> {
  return apiGet<PredictionListResponse>(`/predictions/recent?skip=${skip}&limit=${limit}`);
}

export async function updatePrediction(
  id: string,
  data: PredictionUpdate
): Promise<PredictionRead> {
  return apiPatch<PredictionRead>(`/predictions/${id}`, data);
}
