/**
 * @fileoverview AdminRoute — Guardia de ruta exclusiva para administradores.
 *
 * Verifica autenticación Y rol de admin antes de renderizar la ruta hija.
 * Si el rol no es 'admin', redirige al dashboard principal con un toast de error.
 *
 * Ver ProtectedRoute.tsx para la documentación completa del patrón Route Guard.
 */

import { Outlet, Navigate, useLocation } from "react-router";
import { useAuth } from "../contexts/AuthContext";
import { toast } from "sonner";

/**
 * Componente guardia para rutas exclusivas de administradores.
 *
 * Flujo de decisión:
 *   - Sin token             → Redirigir a /login
 *   - Con token, no admin   → Redirigir a / con notificación de error
 *   - Con token, rol admin  → Renderizar la ruta solicitada (<Outlet />)
 *
 * Protege rutas como: /reportes, /usuarios, /pedidos, /perdidas,
 * /devoluciones, /ajustes, /guia-usuario.
 */
export function AdminRoute() {
  const { token, userRole, delegatedModules } = useAuth();
  const location = useLocation();

  // Primera verificación: existencia de sesión
  if (!token) {
    return <Navigate to="/login" replace />;
  }

  // Segunda verificación: privilegios de administrador
  const moduleByPath: Record<string, string> = {
    "/usuarios": "USUARIOS",
    "/reportes": "REPORTES",
    "/auditoria": "AUDITORIA",
  };
  const requiredModule = moduleByPath[location.pathname];
  if (userRole !== "admin" && (!requiredModule || !delegatedModules.includes(requiredModule))) {
    // toast.error se dispara una vez gracias al ciclo de renderizado de React.
    // Al redirigir inmediatamente con Navigate, el componente se desmonta
    // antes de que pueda renderizar de nuevo, evitando toasts duplicados.
    toast.error("Acceso Denegado: Solo los administradores pueden acceder a esta sección.");
    return <Navigate to="/" replace />;
  }

  return <Outlet />;
}
