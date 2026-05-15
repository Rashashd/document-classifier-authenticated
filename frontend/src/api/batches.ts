import { apiGet, apiPatch } from "./client";
import type { BatchListResponse, BatchRead, BatchUpdate } from "./types";

export async function listBatches(skip = 0, limit = 50): Promise<BatchListResponse> {
  return apiGet<BatchListResponse>(`/batches?skip=${skip}&limit=${limit}`);
}

export async function getBatch(id: string): Promise<BatchRead> {
  return apiGet<BatchRead>(`/batches/${id}`);
}

export async function updateBatch(id: string, data: BatchUpdate): Promise<BatchRead> {
  return apiPatch<BatchRead>(`/batches/${id}`, data);
}
