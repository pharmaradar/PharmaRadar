import { NavLink, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Users, FileText, History, Settings, Bot, Compass,
  Flame, Zap, Building2, ChevronLeft, ChevronRight, Sun, Moon, X, LogOut, ShieldCheck,
} from "lucide-react";
import { useAppStore } from "@/store";
import { useAuthStore } from "@/store/auth";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Dashboard"      },
  { to: "/targets",   icon: Users,           label: "Targets"        },
  { to: "/competitors", icon: Building2,     label: "Competitors"    },
  { to: "/reports",   icon: FileText,        label: "Reports"        },
  { to: "/topics",    icon: Compass,         label: "Topic Explorer" },
  { to: "/burning-topics", icon: Zap,        label: "Burning Topics" },
  { to: "/social",    icon: Flame,           label: "Social Trends"  },
  { to: "/history",   icon: History,         label: "Run History",    admin: true },
  { to: "/agent",     icon: Bot,             label: "Hermes AI"      },
  { to: "/users",     icon: ShieldCheck,     label: "Users",          admin: true },
  { to: "/settings",  icon: Settings,        label: "Settings",       admin: true },
];

export default function Sidebar() {
  const { sidebarOpen, setSidebarOpen, darkMode, toggleDarkMode, mobileMenuOpen, setMobileMenuOpen } = useAppStore();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const isAdmin = user?.role === "admin";
  const nav = NAV.filter((n) => !n.admin || isAdmin);

  return (
    <>
      {/* Mobile backdrop — tap to close */}
      {mobileMenuOpen && (
        <div
          className="lg:hidden fixed inset-0 z-30 bg-black/40 backdrop-blur-sm"
          onClick={() => setMobileMenuOpen(false)}
        />
      )}

      <aside
        className={cn(
          "fixed top-0 left-0 h-full flex flex-col transition-all duration-200 z-40",
          "glass-panel rounded-r-2xl border-y-0 border-l-0 shadow-[4px_0_24px_-8px_rgba(0,0,0,0.1)]",
          "text-slate-800 dark:text-white",
          // Desktop: always visible, width toggles
          "lg:translate-x-0",
          sidebarOpen ? "lg:w-64" : "lg:w-16",
          // Mobile: fixed width, slides in/out
          "w-64",
          mobileMenuOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
        )}
      >
        {/* Logo */}
        <div className="flex items-center gap-3 px-4 py-5 border-b border-slate-200/50 dark:border-white/10 overflow-hidden">
          <div className={cn(
            "w-8 h-8 rounded-lg flex items-center justify-center font-bold text-sm shrink-0",
            "bg-blue-100 text-blue-700 dark:bg-[#2563eb]/30 dark:text-[#93c5fd] shadow-sm"
          )}>
            PR
          </div>
          <div className={cn("flex-1 min-w-0 overflow-hidden", !sidebarOpen && "lg:hidden")}>
            <span className="font-bold text-sm tracking-wide block truncate text-slate-800 dark:text-white">PharmaRadar</span>
            <span className="text-[10px] text-slate-500 dark:text-slate-400 font-medium block truncate">Pharma Intelligence</span>
          </div>
          {/* Close button — mobile only */}
          <button
            className="lg:hidden p-1 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 shrink-0"
            onClick={() => setMobileMenuOpen(false)}
            aria-label="Close menu"
          >
            <X size={18} />
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-4 space-y-0.5 px-2 overflow-y-auto">
          {nav.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              onClick={() => setMobileMenuOpen(false)}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all duration-150",
                  isActive
                    ? "bg-blue-50 text-blue-700 dark:bg-[#2563eb]/20 dark:text-[#93c5fd] font-semibold shadow-sm"
                    : "text-slate-600 dark:text-slate-400 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/5 dark:hover:text-white"
                )
              }
            >
              <Icon size={18} className="shrink-0" />
              <span className={cn("truncate", !sidebarOpen && "lg:hidden")}>{label}</span>
            </NavLink>
          ))}
        </nav>

        {/* Bottom controls */}
        <div className="border-t border-slate-200/50 dark:border-white/10">
          {/* Current user → profile + logout */}
          {user && (
            <div className="flex items-center gap-2.5 px-4 py-3 border-b border-slate-200/50 dark:border-white/10">
              <button
                onClick={() => { navigate("/profile"); setMobileMenuOpen(false); }}
                className="flex items-center gap-2.5 flex-1 min-w-0 group text-left"
                title="View profile"
              >
                <div className="w-7 h-7 rounded-full bg-pharma-blue/15 text-pharma-blue dark:text-blue-300 flex items-center justify-center text-xs font-bold shrink-0 uppercase">
                  {user.email.slice(0, 1)}
                </div>
                <div className={cn("flex-1 min-w-0", !sidebarOpen && "lg:hidden")}>
                  <div className="text-xs font-medium text-slate-700 dark:text-slate-200 truncate group-hover:text-pharma-blue transition-colors" title={user.email}>{user.email}</div>
                  <div className="text-[10px] text-slate-400 capitalize">{user.role}</div>
                </div>
              </button>
              <button
                onClick={() => { logout(); navigate("/login", { replace: true }); }}
                className={cn("p-1.5 text-slate-400 hover:text-red-500 transition-colors shrink-0", !sidebarOpen && "lg:hidden")}
                title="Sign out" aria-label="Sign out"
              >
                <LogOut size={16} />
              </button>
            </div>
          )}
          <button
            onClick={toggleDarkMode}
            className="w-full flex items-center gap-3 px-5 py-3 text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-amber-400 hover:bg-slate-50 dark:hover:bg-white/5 transition-colors text-sm"
            aria-label="Toggle dark mode"
          >
            {darkMode
              ? <Sun size={16} className="shrink-0 text-amber-500" />
              : <Moon size={16} className="shrink-0" />}
            <span className={cn(!sidebarOpen && "lg:hidden")}>
              {darkMode ? "Light mode" : "Dark mode"}
            </span>
          </button>

          {/* Collapse toggle — desktop only */}
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="hidden lg:flex w-full items-center justify-center p-4 text-slate-400 dark:text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 border-t border-slate-200/50 dark:border-white/10"
            aria-label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
          >
            {sidebarOpen ? <ChevronLeft size={18} /> : <ChevronRight size={18} />}
          </button>
        </div>
      </aside>
    </>
  );
}
// Just testing
