import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";

/**
 * Per-user daily AI-generation quota. `can(feature)` is true if the user may
 * still force a fresh regenerate today (admins always can). Call `spent()`
 * after a successful regenerate to refresh the flags so the button hides.
 */
export function useGenQuota() {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const { data } = useQuery({
    queryKey: ["gen-quota"],
    queryFn: api.genQuota,
    staleTime: 60_000,
  });
  const can = (feature: string) => isAdmin || (data?.features?.[feature] ?? true);
  const spent = () => qc.invalidateQueries({ queryKey: ["gen-quota"] });
  return { isAdmin, can, spent };
}
