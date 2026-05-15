import { apiGet, apiPost } from "./client";
import type { UserRead, UserRole } from "./types";

export async function listUsers(): Promise<UserRead[]> {
  return apiGet<UserRead[]>("/users");
}

export async function setUserRole(userId: string, role: UserRole): Promise<UserRead> {
  return apiPost<UserRead>(`/users/admin/${userId}/role`, { role });
}
