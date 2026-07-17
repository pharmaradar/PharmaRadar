import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Pencil, Plus, Save, Trash2, Users, X } from "lucide-react";
import { api, Congress } from "@/lib/api";

export default function QuestionEditor({ congress }: { congress: Congress }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingText, setEditingText] = useState("");

  const addMut = useMutation({
    mutationFn: () => api.congress.addQuestion(congress.id, draft.trim()),
    onSuccess: () => { setDraft(""); qc.invalidateQueries({ queryKey: ["congress"] }); },
  });
  const updateMut = useMutation({
    mutationFn: () => api.congress.updateQuestion(congress.id, editingId!, editingText.trim()),
    onSuccess: () => {
      setEditingId(null);
      setEditingText("");
      qc.invalidateQueries({ queryKey: ["congress"] });
    },
  });
  const removeMut = useMutation({
    mutationFn: (questionId: number) => api.congress.removeQuestion(congress.id, questionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["congress"] }),
  });

  return (
    <div className="border-t border-slate-200/60 dark:border-white/10 px-5 py-4">
      <div className="flex items-center gap-2 mb-3">
        <Users size={16} className="text-pharma-blue dark:text-blue-300" />
        <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Questions for this congress</h4>
      </div>
      <div className="space-y-2">
        {congress.questions.map((question, index) => (
          <div key={question.id} className="flex items-start gap-2 text-sm">
            <span className="text-slate-400 pt-2 w-5 shrink-0">{index + 1}.</span>
            {editingId === question.id ? (
              <input
                value={editingText}
                onChange={(event) => setEditingText(event.target.value)}
                className="flex-1 px-3 py-1.5 border border-pharma-blue/40 rounded-lg bg-transparent"
                autoFocus
              />
            ) : (
              <div className="flex-1 py-1.5 text-slate-700 dark:text-slate-200">{question.question_text}</div>
            )}
            {editingId === question.id ? (
              <>
                <button
                  onClick={() => editingText.trim() && updateMut.mutate()}
                  disabled={!editingText.trim() || updateMut.isPending}
                  className="p-1.5 text-green-600 hover:text-green-700 disabled:opacity-40"
                  title="Save question"
                >
                  <Save size={15} />
                </button>
                <button
                  onClick={() => setEditingId(null)}
                  className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
                  title="Cancel editing"
                >
                  <X size={15} />
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={() => { setEditingId(question.id); setEditingText(question.question_text); }}
                  className="p-1.5 text-slate-400 hover:text-pharma-blue"
                  title="Edit question"
                >
                  <Pencil size={15} />
                </button>
                <button
                  onClick={() => removeMut.mutate(question.id)}
                  disabled={removeMut.isPending}
                  className="p-1.5 text-slate-400 hover:text-red-500 disabled:opacity-40"
                  title="Remove question"
                >
                  <Trash2 size={15} />
                </button>
              </>
            )}
          </div>
        ))}
      </div>
      <div className="flex gap-2 mt-3">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && draft.trim() && !addMut.isPending) addMut.mutate();
          }}
          placeholder="Add a question, e.g. What were the top 10 studies posted on social media?"
          className="flex-1 px-3 py-2 border border-slate-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
        />
        <button
          onClick={() => draft.trim() && addMut.mutate()}
          disabled={!draft.trim() || addMut.isPending}
          className="flex items-center gap-1.5 px-3 py-2 bg-slate-100 dark:bg-white/10 text-slate-700 dark:text-slate-200 rounded-lg text-sm disabled:opacity-50"
        >
          {addMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
          Add
        </button>
      </div>
      {(addMut.isError || updateMut.isError || removeMut.isError) && (
        <div className="text-xs text-red-500 mt-2">Could not update the questions.</div>
      )}
    </div>
  );
}
