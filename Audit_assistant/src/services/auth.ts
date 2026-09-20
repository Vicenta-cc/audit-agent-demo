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
  expires_at: string;
  created_at: string;
  updated_at: string;
};

export async function login(username: string, password: string) {
  const payload = await apiRequest<{ user: ApplicationUser; csrf_token: string }>(
    "/api/auth/login",
    {
      method: "POST",
      body: JSON.stringify({ username, password })
    }
  );
  activateAuthenticatedUser(payload.user.id);
  setCsrfToken(payload.csrf_token);
  return payload.user;
}

export async function currentUser() {
  const payload = await apiRequest<{ user: ApplicationUser }>("/api/auth/me");
  activateAuthenticatedUser(payload.user.id);
  return payload.user;
}

export async function bootstrapAuthentication() {
  const [user, csrf] = await Promise.all([
    currentUser(),
    apiRequest<{ csrf_token: string }>("/api/auth/csrf")
  ]);
  setCsrfToken(csrf.csrf_token);
  return user;
}

export async function logout() {
  try {
    await apiRequest<void>("/api/auth/logout", { method: "POST" });
  } finally {
    clearAuthenticationState();
  }
}
