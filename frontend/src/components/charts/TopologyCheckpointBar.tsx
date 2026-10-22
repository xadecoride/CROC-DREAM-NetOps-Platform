import React, { useEffect, useState, useCallback } from 'react';
import { BookmarkPlus, RotateCcw, X, History } from 'lucide-react';
import { api, getErrorMessage, type TopologyCheckpoint } from '../../api';

interface TopologyCheckpointBarProps {
  onReloadGraph: () => Promise<void>;
  userRole?: string;
}

export const TopologyCheckpointBar: React.FC<TopologyCheckpointBarProps> = ({ onReloadGraph, userRole }) => {
  const isViewer = userRole === 'viewer';
  const [checkpoints, setCheckpoints] = useState<TopologyCheckpoint[]>([]);
  const [customName, setCustomName] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const loadCheckpoints = useCallback(async () => {
    try {
      const res = await api.getTopologyCheckpoints();
      setCheckpoints(Array.isArray(res) ? res : (res.checkpoints || []));
    } catch {
      // Ignore if endpoint not ready or offline
    }
  }, []);

  useEffect(() => {
    void loadCheckpoints();
  }, [loadCheckpoints]);

  const handleCreate = async () => {
    if (isViewer) return;
    setBusy(true);
    setMsg(null);
    try {
      const name = customName.trim() || `Снимок #${checkpoints.length + 1}`;
      await api.createTopologyCheckpoint(name);
      setCustomName('');
      await loadCheckpoints();
      setMsg(`Сохранён «${name}»`);
    } catch (err: unknown) {
      setMsg(getErrorMessage(err, 'Ошибка создания снимка'));
    } finally {
      setBusy(false);
    }
  };

  const handleRestore = async (cp: TopologyCheckpoint) => {
    if (isViewer) return;
    setBusy(true);
    setMsg(null);
    try {
      await api.restoreTopologyCheckpoint(cp.id);
      await onReloadGraph();
      setMsg(`Откат к «${cp.name}» выполнен`);
    } catch (err: unknown) {
      setMsg(getErrorMessage(err, 'Ошибка отката снимка'));
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (isViewer) return;
    setBusy(true);
    setMsg(null);
    try {
      await api.deleteTopologyCheckpoint(id);
      await loadCheckpoints();
    } catch (err: unknown) {
      setMsg(getErrorMessage(err, 'Ошибка удаления снимка'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-testid="topology-checkpoint-bar"
      className="flex flex-wrap items-center justify-between gap-2 px-3 py-1.5 rounded-lg bg-[var(--bg-main)]/70 border border-[var(--border-subtle)] text-[11px] font-mono"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="inline-flex items-center gap-1 text-[var(--text-muted)] mr-1">
          <History className="w-3.5 h-3.5 text-zinc-400" />
          <span>Точки отката:</span>
        </span>

        <input
          type="text"
          value={customName}
          onChange={(e) => setCustomName(e.target.value)}
          placeholder={`Точка #${checkpoints.length + 1}`}
          data-testid="checkpoint-name-input"
          disabled={isViewer}
          className="w-28 px-2 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[10px] text-[var(--text-primary)] outline-none focus:border-zinc-600 disabled:opacity-50"
        />

        <button
          type="button"
          disabled={busy || isViewer}
          onClick={() => void handleCreate()}
          data-testid="create-checkpoint-btn"
          title={isViewer ? 'Недоступно в режиме просмотра' : undefined}
          className={`px-2 py-0.5 rounded bg-zinc-800 border border-zinc-700 text-zinc-200 font-medium inline-flex items-center gap-1 ${
            isViewer ? 'opacity-40 cursor-not-allowed' : 'hover:bg-zinc-700 cursor-pointer disabled:opacity-50'
          }`}
        >
          <BookmarkPlus className="w-3 h-3" />
          <span>Сохранить</span>
        </button>

        {checkpoints.map((cp) => (
          <div
            key={cp.id}
            data-testid={`checkpoint-pill-${cp.id}`}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[10px] text-[var(--text-primary)]"
          >
            <span className="font-semibold">{cp.name}</span>
            <button
              type="button"
              disabled={busy || isViewer}
              onClick={() => void handleRestore(cp)}
              data-testid={`restore-checkpoint-${cp.id}`}
              className={`px-1.5 py-0.2 rounded bg-zinc-800 text-zinc-200 border border-zinc-700 inline-flex items-center gap-0.5 ${
                isViewer ? 'opacity-40 cursor-not-allowed' : 'hover:bg-zinc-700 cursor-pointer disabled:opacity-50'
              }`}
              title={isViewer ? 'Недоступно в режиме просмотра' : 'Откатить к этому снимку'}
            >
              <RotateCcw className="w-2.5 h-2.5" />
              <span>Откат</span>
            </button>
            <button
              type="button"
              disabled={busy || isViewer}
              onClick={() => void handleDelete(cp.id)}
              data-testid={`delete-checkpoint-${cp.id}`}
              className={`text-[var(--text-muted)] px-0.5 ${
                isViewer ? 'opacity-40 cursor-not-allowed' : 'hover:text-rose-400 cursor-pointer disabled:opacity-50'
              }`}
              title={isViewer ? 'Недоступно в режиме просмотра' : 'Удалить'}
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        ))}
      </div>

      {msg && <span className="text-[10px] text-zinc-300">{msg}</span>}
    </div>
  );
};
