// This is a placeholder file for authentication logic.

export const storeTokens = (accessToken: string, refreshToken: string) => {
  if (typeof window !== "undefined") {
    localStorage.setItem("access_token", accessToken);
    localStorage.setItem("refresh_token", refreshToken);
    // A fresh session: whatever profile is cached belongs to the previous
    // session (possibly a different user, or the same user before an admin
    // changed their role). Callers re-fetch /auth/me right after this.
    localStorage.removeItem("user_profile");
  }
};

export const getAccessToken = (): string | null => {
  if (typeof window !== "undefined") {
    return localStorage.getItem("access_token");
  }
  return null;
};

export const getRefreshToken = (): string | null => {
  if (typeof window !== "undefined") {
    return localStorage.getItem("refresh_token");
  }
  return null;
};

// Fired on window whenever the cached profile changes, so anything that
// derives UI from it (e.g. useRole() in lib/permissions.ts) re-reads it —
// a role changed by an admin is picked up on the next /auth/me call
// without needing a reload.
export const PROFILE_UPDATED_EVENT = "dms:profile-updated";

export const setUserProfile = (profile: any) => {
  if (typeof window !== "undefined") {
    try {
      localStorage.setItem("user_profile", JSON.stringify(profile));
    } catch (_) {}
    window.dispatchEvent(new Event(PROFILE_UPDATED_EVENT));
  }
};

export const getUserProfile = (): any | null => {
  if (typeof window !== "undefined") {
    try {
      const raw = localStorage.getItem("user_profile");
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }
  return null;
};

export const clearTokens = () => {
  if (typeof window !== "undefined") {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    localStorage.removeItem("user_profile");
    sessionStorage.clear();
    window.dispatchEvent(new Event(PROFILE_UPDATED_EVENT));
  }
};

export const isAuthenticated = (): boolean => {
  if (typeof window !== "undefined") {
    const acc = localStorage.getItem("access_token");
    const ref = localStorage.getItem("refresh_token");
    return !!(acc || ref);
  }
  return false;
};