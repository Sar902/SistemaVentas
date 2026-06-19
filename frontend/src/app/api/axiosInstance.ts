/**
 * @fileoverview Instancia global de Axios con interceptores JWT.
 *
 * Este módulo es el único punto de contacto entre el frontend y el backend.
 * Toda petición HTTP del sistema pasa por esta instancia, garantizando que:
 *   1. El token de autenticación se adjunte automáticamente a cada petición.
 *   2. Las sesiones expiradas se renueven silenciosamente sin expulsar al usuario.
 *
 * ARQUITECTURA DE AUTENTICACIÓN JWT:
 *   - Access Token (1 día):  Se envía en cada petición como `Authorization: Bearer <token>`.
 *   - Refresh Token (7 días): Se guarda en localStorage. Solo se usa cuando el
 *     access token expira (respuesta 401 del servidor).
 *
 * PROXY DE VITE:
 *   `baseURL: "/api"` funciona junto con la configuración del proxy en vite.config.ts,
 *   que redirige `/api/*` → `http://localhost:8000/api/*`. Esto evita hardcodear
 *   la URL del servidor, haciendo el código portable entre entornos.
 */
/**
 * @fileoverview Instancia global de Axios con interceptores JWT.
 */

import axios from "axios";

// Instancia principal de Axios
const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "/api",
});

// ─── REQUEST INTERCEPTOR ─────────────────────────────────────────────────────
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem("accessToken");
    if (token) {
      config.headers["Authorization"] = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// ─── RESPONSE INTERCEPTOR ────────────────────────────────────────────────────
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    // 1. Evitar bucle si estamos intentando refrescar o loguear
    if (
      originalRequest?.url?.includes("/token/") || 
      error.response?.status !== 401 || 
      originalRequest._retry
    ) {
      return Promise.reject(error);
    }

    // 2. Marcar petición como reintentada
    originalRequest._retry = true;

    try {
      const refreshToken = localStorage.getItem("refreshToken");
      if (!refreshToken) throw new Error("No refresh token");

      // IMPORTANTE: Usamos 'axios' original para evitar que el interceptor 
      // de la instancia 'api' intente capturar esta llamada interna.
      const { data } = await axios.post(
        `${import.meta.env.VITE_API_URL || "/api"}/token/refresh/`, 
        { refresh: refreshToken }
      );

      // 3. Guardar nuevo token y reintentar la petición original
      localStorage.setItem("accessToken", data.access);
      originalRequest.headers["Authorization"] = `Bearer ${data.access}`;
      
      return api(originalRequest);
    } catch (refreshError) {
      // 4. Si el refresh falla, sesión terminada
      localStorage.removeItem("accessToken");
      localStorage.removeItem("refreshToken");
      window.location.href = "/login";
      return Promise.reject(refreshError);
    }
  }
);

export default api;
