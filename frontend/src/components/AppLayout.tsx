import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import {
  FileText,
  Layers,
  Users,
  ClipboardList,
  LogOut,
  ScanLine,
  Play,
} from "lucide-react";
import { cn } from "@/lib/utils";

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
    isActive
      ? "bg-primary text-primary-foreground"
      : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
  );

export function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const isAdmin = user?.role === "admin";
  const canAudit = user?.role === "admin" || user?.role === "auditor";

  return (
    <div className="flex h-screen bg-background">
      <aside className="flex w-56 flex-col border-r bg-card px-3 py-4">
        <div className="mb-6 flex items-center gap-2 px-3">
          <ScanLine className="h-5 w-5 text-primary" />
          <span className="font-semibold text-sm">Doc Classifier</span>
        </div>

        <nav className="flex flex-1 flex-col gap-1">
          <NavLink to="/batches" className={navLinkClass}>
            <Layers className="h-4 w-4" />
            Batches
          </NavLink>
          <NavLink to="/predictions" className={navLinkClass}>
            <FileText className="h-4 w-4" />
            Predictions
          </NavLink>
          <NavLink to="/demo" className={navLinkClass}>
            <Play className="h-4 w-4" />
            Demo
          </NavLink>
          {isAdmin && (
            <NavLink to="/users" className={navLinkClass}>
              <Users className="h-4 w-4" />
              Users
            </NavLink>
          )}
          {canAudit && (
            <NavLink to="/audit" className={navLinkClass}>
              <ClipboardList className="h-4 w-4" />
              Audit Log
            </NavLink>
          )}
        </nav>

        <Separator className="my-3" />

        <div className="flex flex-col gap-2 px-1">
          <div className="flex items-center gap-2">
            <div className="flex-1 min-w-0">
              <p className="truncate text-xs font-medium">{user?.email}</p>
            </div>
            <Badge variant="secondary" className="shrink-0 text-[10px] capitalize">
              {user?.role}
            </Badge>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start gap-2 text-muted-foreground"
            onClick={handleLogout}
          >
            <LogOut className="h-4 w-4" />
            Sign out
          </Button>
        </div>
      </aside>

      <main className="flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
