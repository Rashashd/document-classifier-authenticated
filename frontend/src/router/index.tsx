import { createBrowserRouter, Navigate } from "react-router-dom";
import { ProtectedRoute } from "./ProtectedRoute";
import { AppLayout } from "@/components/AppLayout";
import { LoginPage } from "@/pages/LoginPage";
import { RegisterPage } from "@/pages/RegisterPage";
import { BatchesPage } from "@/pages/BatchesPage";
import { BatchDetailPage } from "@/pages/BatchDetailPage";
import { PredictionsPage } from "@/pages/PredictionsPage";
import { UsersPage } from "@/pages/UsersPage";
import { AuditPage } from "@/pages/AuditPage";

export const router = createBrowserRouter([
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    path: "/register",
    element: <RegisterPage />,
  },
  {
    path: "/",
    element: (
      <ProtectedRoute>
        <AppLayout />
      </ProtectedRoute>
    ),
    children: [
      { index: true, element: <Navigate to="/batches" replace /> },
      {
        path: "batches",
        element: <BatchesPage />,
      },
      {
        path: "batches/:id",
        element: <BatchDetailPage />,
      },
      {
        path: "predictions",
        element: <PredictionsPage />,
      },
      {
        path: "users",
        element: (
          <ProtectedRoute roles={["admin"]}>
            <UsersPage />
          </ProtectedRoute>
        ),
      },
      {
        path: "audit",
        element: (
          <ProtectedRoute roles={["admin", "auditor"]}>
            <AuditPage />
          </ProtectedRoute>
        ),
      },
    ],
  },
]);
