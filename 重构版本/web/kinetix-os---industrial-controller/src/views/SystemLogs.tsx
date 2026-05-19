import { useEffect, useMemo, useState } from 'react';
import { Download, RefreshCw, Terminal, Trash2 } from 'lucide-react';
import { fetchRecentLogs } from '../api/client';
import type { WebLogEntry } from '../api/client';

export default function SystemLogs() {
  const [logs, setLogs] = useState<WebLogEntry[]>([]);
  const [logPath, setLogPath] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const refresh = () => {
    setLoading(true);
    setError('');
    fetchRecentLogs(300)
      .then((payload) => {
        setLogs(payload.entries.reverse());
        setLogPath(payload.log_path);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
  }, []);

  const summary = useMemo(() => {
    const success = logs.filter((log) => isSuccess(log.result)).length;
    const failed = logs.length - success;
    const last = logs[0]?.ts || logs[0]?.time || '-';
    return { success, failed, last };
  }, [logs]);

  return (
    <div className="flex h-full overflow-hidden bg-[#f0f4f8]">
      <main className="flex min-w-0 flex-1 flex-col gap-4 overflow-hidden p-6">
        <section className="grid grid-cols-12 gap-4">
          <div className="industrial-card col-span-5 p-4">
            <div className="mb-3 flex items-center gap-2">
              <Terminal size={18} className="text-primary-container" />
              <h2 className="text-base font-bold text-on-surface">日志说明</h2>
            </div>
            <p className="truncate text-xs text-text-secondary">{logPath || 'Web API 日志尚未创建'}</p>
            <p className="mt-2 text-xs text-text-secondary">记录 Web API、NLP、控制桥接和系统动作，后续与 Qt 操作日志统一。</p>
          </div>

          <div className="industrial-card col-span-4 p-4">
            <h2 className="mb-3 text-base font-bold text-on-surface">日志摘要</h2>
            <div className="grid grid-cols-3 gap-3">
              <SummaryCell label="总数" value={String(logs.length)} />
              <SummaryCell label="成功" value={String(summary.success)} />
              <SummaryCell label="失败" value={String(summary.failed)} tone={summary.failed > 0 ? 'danger' : 'default'} />
            </div>
            <div className="mt-3 rounded border border-border-divider bg-white px-3 py-2 text-xs">
              <span className="mr-2 font-bold text-text-secondary">最近时间</span>
              <span className="font-mono">{summary.last}</span>
            </div>
          </div>

          <div className="industrial-card col-span-3 p-4">
            <h2 className="mb-3 text-base font-bold text-on-surface">日志操作</h2>
            <div className="grid grid-cols-3 gap-2">
              <button onClick={refresh} className="flex h-9 items-center justify-center rounded border border-border-divider bg-white hover:bg-surface">
                <RefreshCw size={16} />
              </button>
              <button disabled className="flex h-9 items-center justify-center rounded border border-border-divider bg-white opacity-70">
                <Download size={16} />
              </button>
              <button disabled className="flex h-9 items-center justify-center rounded border border-danger/30 bg-danger/5 text-danger opacity-70">
                <Trash2 size={16} />
              </button>
            </div>
          </div>
        </section>

        {error && <div className="rounded border border-danger/20 bg-danger/5 p-3 text-xs font-bold text-danger">{error}</div>}

        <section className="industrial-card flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="border-b border-border-divider px-4 py-3">
            <h3 className="text-sm font-bold">操作日志</h3>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            <table className="w-full border-collapse text-left">
              <thead className="sticky top-0 border-b border-border-divider bg-[#f8fafc]">
                <tr className="text-[10px] font-bold uppercase text-text-secondary">
                  <th className="px-4 py-3">时间</th>
                  <th className="px-4 py-3">类别</th>
                  <th className="px-4 py-3">操作</th>
                  <th className="px-4 py-3">结果</th>
                  <th className="px-4 py-3">详情说明</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-divider font-mono text-xs">
                {logs.map((log, i) => {
                  const ok = isSuccess(log.result);
                  return (
                    <tr key={`${log.ts}-${i}`} className={`transition-colors ${ok ? 'hover:bg-success-light/50' : 'hover:bg-danger-light/50'}`}>
                      <td className="whitespace-nowrap px-4 py-3 text-text-secondary">{log.ts || log.time}</td>
                      <td className="px-4 py-3">{log.category}</td>
                      <td className="px-4 py-3">{log.action}</td>
                      <td className="px-4 py-3">
                        <span className={`rounded px-2 py-0.5 text-[10px] font-bold ${ok ? 'bg-success-light text-primary' : 'bg-danger-light text-danger'}`}>
                          {log.result}
                        </span>
                      </td>
                      <td className="max-w-sm truncate px-4 py-3 text-on-surface-variant">{log.detail}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {!logs.length && (
              <div className="flex flex-col items-center justify-center py-24 text-status-disabled">
                <Terminal size={36} className="mb-3 opacity-30" />
                <p className="text-sm">{loading ? '日志加载中' : '暂无 Web API 日志'}</p>
              </div>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}

function SummaryCell({ label, value, tone = 'default' }: { label: string; value: string; tone?: 'default' | 'danger' }) {
  return (
    <div className="rounded border border-border-divider bg-white px-3 py-2 text-xs">
      <div className="mb-1 text-[10px] font-bold text-text-secondary">{label}</div>
      <div className={`font-mono text-base font-bold ${tone === 'danger' ? 'text-danger' : 'text-on-surface'}`}>{value}</div>
    </div>
  );
}

function isSuccess(result: string) {
  return ['success', 'accepted', 'dry_run'].includes(String(result).toLowerCase()) || result === '成功';
}
