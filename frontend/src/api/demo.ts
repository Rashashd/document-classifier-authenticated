import { apiGet, apiPost } from "./client";
import type { BatchListResponse } from "./types";

export interface TriggerResult {
  batch_id: string;
  job_id: string;
  filename: string;
}

export interface QueueStats {
  pending: number;
  processing: number;
  done: number;
  failed: number;
}

export async function triggerDemo(): Promise<TriggerResult> {
  return apiPost<TriggerResult>("/demo/trigger", {});
}

export async function getQueueStats(): Promise<QueueStats> {
  return apiGet<QueueStats>("/demo/queue");
}

export async function getDemoBatches(limit = 20): Promise<BatchListResponse> {
  return apiGet<BatchListResponse>(`/demo/batches?limit=${limit}`);
}
