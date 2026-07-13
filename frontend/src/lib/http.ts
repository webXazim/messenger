import axios from "axios";
import { API_BASE_URL, AUTH_API_BASE_URL } from "./config";
import { unwrapData } from "./apiResponse";
import { clearTokens, getAccessToken, getRefreshToken, setAccessToken, setRefreshToken } from "./tokenStore";

type RetryConfig = { _retry?: boolean; headers?: Record<string, string> };

export const http = axios.create({
  baseURL: API_BASE_URL,
  timeout: 20000,
});

let refreshPromise: Promise<string | null> | null = null;

http.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

http.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = (error.config ?? {}) as RetryConfig;
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;
      if (!refreshPromise) {
        const refresh = getRefreshToken();
        refreshPromise = refresh
          ? axios.post(`${AUTH_API_BASE_URL}/auth/token/refresh/`, { refresh })
              .then((response) => {
                const payload = unwrapData<{ access?: string; refresh?: string }>(response.data);
                const access = payload.access || "";
                const rotatedRefresh = payload.refresh;
                if (!access) throw new Error("Central token refresh did not return an access token.");
                setAccessToken(access);
                if (rotatedRefresh) setRefreshToken(rotatedRefresh);
                return access;
              })
              .catch(() => {
                clearTokens();
                return null;
              })
              .finally(() => {
                refreshPromise = null;
              })
          : Promise.resolve(null);
      }
      const access = await refreshPromise;
      if (access) {
        originalRequest.headers = { ...(originalRequest.headers ?? {}), Authorization: `Bearer ${access}` };
        return http(originalRequest);
      }
    }
    return Promise.reject(error);
  },
);
