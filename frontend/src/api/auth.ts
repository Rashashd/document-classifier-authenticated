import { apiFormPost, apiGet, apiFetch } from "./client";
import type { LoginResponse, UserRead, RegisterRequest } from "./types";

export async function login(email: string, password: string): Promise<LoginResponse> {
  const form = new URLSearchParams({ username: email, password });
  return apiFormPost<LoginResponse>("/auth/login", form);
}

export async function register(data: RegisterRequest): Promise<UserRead> {
  return apiFetch<UserRead>("/auth/register", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function logout(): Promise<void> {
  await apiFetch("/auth/logout", { method: "POST" });
}

export async function getMe(): Promise<UserRead> {
  return apiGet<UserRead>("/users/me");
}
