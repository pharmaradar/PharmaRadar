import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AppState {
  sidebarOpen: boolean;
  mobileMenuOpen: boolean;
  activeRunId: number | null;
  darkMode: boolean;
  socialLang: string;  // "fr" | "en" | "all" — shared between Social pages and Run Scan
  setSidebarOpen: (v: boolean) => void;
  setMobileMenuOpen: (v: boolean) => void;
  setActiveRunId: (id: number | null) => void;
  toggleDarkMode: () => void;
  setSocialLang: (v: string) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      mobileMenuOpen: false,
      activeRunId: null,
      darkMode: false,
      socialLang: "all",
      setSidebarOpen: (v) => set({ sidebarOpen: v }),
      setMobileMenuOpen: (v) => set({ mobileMenuOpen: v }),
      setActiveRunId: (id) => set({ activeRunId: id }),
      toggleDarkMode: () => set((s) => ({ darkMode: !s.darkMode })),
      setSocialLang: (v) => set({ socialLang: v }),
    }),
    { name: "pharmaradar-ui", version: 2, migrate: (s: any) => ({ ...s, socialLang: "all" }) }
  )
);
