import { apiGet } from "./client";
import type { AuditEntryRead } from "./types";

export async function listAuditEntries(): Promise<AuditEntryRead[]> {
  return apiGet<AuditEntryRead[]>("/audit");
}
