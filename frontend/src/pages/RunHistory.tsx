import { useQuery } from "@tanstack/react-query";
import { api, type RunOut } from "@/lib/api";
import { formatDateTime, cn } from "@/lib/utils";

const STATUS_COLORS: Record<string, string> = {
  success: "bg-green-50 text-green-700",
  running: "bg-blue-50 text-blue-700",
  error: "bg-red-50 text-red-700",
  cancelled: "bg-gray-100 text-gray-600",
};

export default function RunHistory() {
  const { data: runs, isLoading } = useQuery({ queryKey: ["runs"], queryFn: api.runs.list });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-pharma-blue dark:text-[#e2e8f0]">Run History</h1>

      {isLoading ? (
        <div className="text-center py-12 text-gray-400">Loading...</div>
      ) : !runs?.length ? (
        <div className="text-center py-12 text-gray-400">No runs yet.</div>
      ) : (
        <div className="space-y-3">
          {runs.map((run) => (
            <RunCard key={run.id} run={run} />
          ))}
        </div>
      )}
    </div>
  );
}

function RunCard({ run }: { run: RunOut }) {
  return (
    <div className="glass rounded-xl p-5 shadow-sm border border-slate-200/50 dark:border-white/10">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <span className="font-semibold text-sm">Run #{run.id}</span>
            <span className={cn("text-xs px-2 py-0.5 rounded-full", STATUS_COLORS[run.status] ?? STATUS_COLORS.cancelled)}>
              {run.status}
            </span>
          </div>
          <div className="text-xs text-gray-500">
            Started: {formatDateTime(run.started_at)}
            {run.completed_at && ` · Completed: ${formatDateTime(run.completed_at)}`}
          </div>
        </div>
        <div className="grid grid-cols-3 gap-x-6 text-right text-xs">
          <div>
            <div className="font-semibold">{run.targets_processed}/{run.total_targets}</div>
            <div className="text-gray-400">targets</div>
          </div>
          <div>
            <div className="font-semibold">{run.insights_extracted}</div>
            <div className="text-gray-400">insights</div>
          </div>
          <div>
            <div className="font-semibold">{run.llm_calls_used}</div>
            <div className="text-gray-400">LLM calls</div>
          </div>
        </div>
      </div>
      {run.error_message && (
        <div className="mt-3 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">
          {run.error_message}
        </div>
      )}
    </div>
  );
}
