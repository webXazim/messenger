import axios from "axios";
import { API_BASE_URL } from "./config";
import { refreshAccessTokenOnce } from "./authRefresh";
import { getAccessToken } from "./tokenStore";

type RetryConfig = { _retry?: boolean; headers?: Record<string, string> };

export const http = axios.create({
  baseURL: API_BASE_URL,
  timeout: 20000,
});

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
      const access = await refreshAccessTokenOnce(true);
      if (access) {
        originalRequest.headers = { ...(originalRequest.headers ?? {}), Authorization: `Bearer ${access}` };
        return http(originalRequest);
      }
    }
    return Promise.reject(error);
  },
);
