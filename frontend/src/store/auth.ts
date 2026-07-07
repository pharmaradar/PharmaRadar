import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface AuthUser {
  id: number;
  name: string | null;
  email: string;
  role: "admin" | "user";
  is_active: boolean;
  is_superadmin?: boolean;
  created_at: string;
}

interface AuthState {
  token: string | null;
  user: AuthUser | null;
  setAuth: (token: string, user: AuthUser) => void;
  setUser: (user: AuthUser) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      setAuth: (token, user) => set({ token, user }),
      setUser: (user) => set({ user }),
      logout: () => set({ token: null, user: null }),
    }),
    { name: "pharmaradar-auth" }
  )
);

export const isAdmin = (): boolean => useAuthStore.getState().user?.role === "admin";
