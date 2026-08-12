import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, Tag } from "lucide-react";
import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";

/**
 * The standing keyword list every scheduled scan uses.
 *
 * This has always existed in Settings, but the client asked "how do we configure
 * standard tracked keywords?" — so it was not discoverable where it mattered.
 * Surfacing it on the Social page, next to the search bar it complements, is the
 * actual fix: these are the terms tracked continuously, while the search bar
 * stays for one-off questions.
 */
export default function StandardKeywords() {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const [draft, setDraft] = useState<string | null>(null);

  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: api.settings.get });
  const keywords = settings?.social_keywords ?? [];

  // Only seed the editor once, so a background settings refetch cannot discard
  // what the user is part-way through typing.
  useEffect(() => {
    if (draft === null && settings) setDraft(keywords.join(", "));
  }, [settings, draft, keywords]);

  const saveMut = useMutation({
    mutationFn: () => api.settings.update({
      social_keywords: (draft ?? "")
        .split(/[,\n]/).map((k) => k.trim()).filter(Boolean),
    }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });

  const parsed = (draft ?? "").split(/[,\n]/).map((k) => k.trim()).filter(Boolean);
  const dirty = draft !== null && parsed.join(",") !== keywords.join(",");

  return (
    <div className="glass rounded-xl p-5 space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-start gap-2.5 min-w-0">
          <div className="p-2 bg-pharma-blue/10 rounded-lg shrink-0">
            <Tag size={16} className="text-pharma-blue dark:text-blue-300" />
          </div>
          <div className="min-w-0">
            <h2 className="font-semibold text-sm">Standard tracked keywords</h2>
            <p className="text-xs text-gray-400">
              Collected on every scheduled scan. The search bar above stays free for one-off questions.
            </p>
          </div>
        </div>
        {isAdmin && dirty && (
          <button onClick={() => saveMut.mutate()} disabled={saveMut.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-pharma-blue text-white rounded-lg disabled:opacity-50">
            {saveMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
            Save {parsed.length} terms
          </button>
        )}
      </div>

      {isAdmin ? (
        <>
          <textarea
            value={draft ?? ""}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            placeholder="cancer du poumon, CBNPC, Tecentriq, Keytruda, Imfinzi…"
            className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent resize-y"
          />
          <p className="text-[11px] text-gray-400">
            {parsed.length} terms — comma or newline separated. Instagram bills per term,
            so keep the list to what is genuinely worth tracking every week.
          </p>
          {saveMut.isError && (
            <p className="text-xs text-red-400">{(saveMut.error as Error)?.message}</p>
          )}
        </>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {keywords.length === 0
            ? <p className="text-sm text-gray-400">No standard keywords configured.</p>
            : keywords.map((keyword) => (
                <span key={keyword}
                  className="px-2 py-0.5 rounded-lg border border-slate-200 dark:border-white/10 text-xs text-slate-600 dark:text-slate-300">
                  {keyword}
                </span>
              ))}
        </div>
      )}
    </div>
  );
}
