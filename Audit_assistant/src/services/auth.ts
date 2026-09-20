import {
  activateAuthenticatedUser,
  apiRequest,
  clearAuthenticationState,
  setCsrfToken
} from "./apiClient";

export type ApplicationUser = {
  id: string;
  username: string;
  role: "admin" | "user";
  status: "active" | "disabled";
  activation_mode: "first_login" | "created_at";
  validity_started_at: string;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
};

export async function login(username: string, password: string, signal?: AbortSignal) {
  const payload = await apiRequest<{ user: ApplicationUser; csrf_token: string }>(
    "/api/auth/login",
    {
      method: "POST",
      signal,
      body: JSON.stringify({ username, password })
    }
  );
  signal?.throwIfAborted();
  activateAuthenticatedUser(payload.user.id);
  setCsrfToken(payload.csrf_token);
  return payload.user;
}

export async function currentUser() {
  const payload = await apiRequest<{ user: ApplicationUser }>("/api/auth/me");
  activateAuthenticatedUser(payload.user.id);
  return payload.user;
}

export async function bootstrapAuthentication(signal?: AbortSignal) {
  const user = await apiRequest<{ user: ApplicationUser }>("/api/auth/me", { signal });
  const csrf = await apiRequest<{ csrf_token: string }>("/api/auth/csrf", {
    signal, headers: { "X-Application-User": user.user.id }
  });
  signal?.throwIfAborted();
  activateAuthenticatedUser(user.user.id);
  setCsrfToken(csrf.csrf_token);
  return user.user;
}

export async function logout() {
  try {
    await apiRequest<void>("/api/auth/logout", { method: "POST" });
  } finally {
    clearAuthenticationState();
  }
}
