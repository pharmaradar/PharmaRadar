import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Menu } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import Dashboard from "@/pages/Dashboard";
import Targets from "@/pages/Targets";
import Reports from "@/pages/Reports";
import RunHistory from "@/pages/RunHistory";
import SettingsPage from "@/pages/Settings";
import Agent from "@/pages/Agent";
import TopicExplorer from "@/pages/TopicExplorer";
import BurningTopics from "@/pages/BurningTopics";
import Competitors from "@/pages/Competitors";
import SocialPage from "@/pages/Social";
import Login from "@/pages/Login";
import Users from "@/pages/Users";
import Profile from "@/pages/Profile";
import { useAppStore } from "@/store";
import { useAuthStore } from "@/store/auth";
import { cn } from "@/lib/utils";
import AnimatedBackground from "@/components/AnimatedBackground";

function Padded({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex-1 overflow-auto h-full">
      <div className="max-w-7xl mx-auto px-4 lg:px-6 py-8">{children}</div>
    </div>
  );
}

/** Admin-only route: non-admins are redirected to the dashboard. */
function AdminRoute({ children }: { children: React.ReactNode }) {
  const role = useAuthStore((s) => s.user?.role);
  return role === "admin" ? <>{children}</> : <Navigate to="/dashboard" replace />;
}

/** Superadmin-only route. Hiding a nav link is not access control — without this
 *  the page is still reachable by typing the URL. */
function SuperadminRoute({ children }: { children: React.ReactNode }) {
  const isSuperadmin = useAuthStore((s) => s.user?.is_superadmin);
  return isSuperadmin ? <>{children}</> : <Navigate to="/dashboard" replace />;
}

function AuthedApp() {
  const { sidebarOpen, setMobileMenuOpen } = useAppStore();
  return (
    <div className="flex h-screen overflow-hidden text-slate-800 dark:text-slate-200">
      <AnimatedBackground />

      {/* Mobile top bar */}
      <div className="lg:hidden fixed top-0 left-0 right-0 h-12 z-20 flex items-center gap-3 px-4 glass-panel border-b border-slate-200/50 dark:border-white/10">
        <button
          onClick={() => setMobileMenuOpen(true)}
          className="p-1.5 text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white"
          aria-label="Open menu"
        >
          <Menu size={22} />
        </button>
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded bg-blue-100 text-blue-700 dark:bg-[#2563eb]/30 dark:text-[#93c5fd] flex items-center justify-center text-[10px] font-bold">
            PR
          </div>
          <span className="font-bold text-sm text-slate-800 dark:text-white">PharmaRadar</span>
        </div>
      </div>

      <Sidebar />

      <main className={cn(
        "flex-1 flex flex-col min-w-0 overflow-hidden transition-all duration-200",
        "mt-12 ml-0",
        "lg:mt-0",
        sidebarOpen ? "lg:ml-64" : "lg:ml-16"
      )}>
        <Routes>
          <Route path="/"          element={<Navigate to="/dashboard" replace />} />
          <Route path="/topics"    element={<TopicExplorer />} />
          <Route path="/burning-topics" element={<Padded><BurningTopics /></Padded>} />
          {/* Congress was merged into Burning Topics — keep old bookmarks/links alive */}
          <Route path="/congress" element={<Navigate to="/burning-topics" replace />} />
          <Route path="/dashboard" element={<Padded><Dashboard /></Padded>} />
          <Route path="/social"    element={<SocialPage />} />
          <Route path="/reports"   element={<Padded><Reports /></Padded>} />
          <Route path="/agent"     element={<Padded><Agent /></Padded>} />
          <Route path="/profile"   element={<Padded><Profile /></Padded>} />
          <Route path="/targets"   element={<Padded><Targets /></Padded>} />
          <Route path="/competitors" element={<Padded><Competitors /></Padded>} />
          {/* Admin-only */}
          <Route path="/history"   element={<SuperadminRoute><Padded><RunHistory /></Padded></SuperadminRoute>} />
          <Route path="/settings"  element={<AdminRoute><Padded><SettingsPage /></Padded></AdminRoute>} />
          <Route path="/users"     element={<AdminRoute><Padded><Users /></Padded></AdminRoute>} />
          <Route path="*"          element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  const { darkMode } = useAppStore();
  const token = useAuthStore((s) => s.token);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode);
  }, [darkMode]);

  return (
    <BrowserRouter>
      {token ? (
        <Routes>
          <Route path="/login" element={<Navigate to="/dashboard" replace />} />
          <Route path="/*" element={<AuthedApp />} />
        </Routes>
      ) : (
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      )}
    </BrowserRouter>
  );
}
