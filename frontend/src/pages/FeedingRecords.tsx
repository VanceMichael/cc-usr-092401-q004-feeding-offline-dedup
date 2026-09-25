import React, { useEffect, useMemo, useState } from 'react';
import { Plus, Edit2, X, AlertTriangle, Clock, Ban, FileSignature, History } from 'lucide-react';
import { feedingRecordApi, dailyFeedingReportApi, batchApi } from '../services/api';
import type { FeedingRecord, FeedingConflict, Batch, DailyFeedingReport } from '../types';

const PAGE_SIZE = 20;

const STATUS_META: Record<string, { label: string; cls: string; title: string }> = {
  late: { label: '迟报', cls: 'bg-amber-100 text-amber-800', title: '批次关闭后补传，待审核' },
  pending: { label: '待审', cls: 'bg-amber-100 text-amber-800', title: '等待审批，暂不计入汇总' },
  rejected: { label: '已驳回', cls: 'bg-gray-200 text-gray-600', title: '审批驳回，不计入汇总' },
  conflict: { label: '冲突', cls: 'bg-red-100 text-red-700', title: '同一设备流水号上送了不同内容' },
  revoked: { label: '已撤销', cls: 'bg-gray-200 text-gray-700 line-through', title: '已撤销，不计入汇总' },
  future: { label: '待生效', cls: 'bg-blue-100 text-blue-700', title: '新版本将在生效时点后影响汇总' },
  revised: { label: `已更正`, cls: 'bg-purple-100 text-purple-700', title: '经更正形成的版本' },
};

const FeedingRecords: React.FC = () => {
  const [records, setRecords] = useState<FeedingRecord[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [conflicts, setConflicts] = useState<FeedingConflict[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [filterBatch, setFilterBatch] = useState<number | ''>('');
  const [toast, setToast] = useState<{ kind: string; text: string } | null>(null);

  const [showModal, setShowModal] = useState(false);
  const [editingRecord, setEditingRecord] = useState<FeedingRecord | null>(null);
  const [effectiveAt, setEffectiveAt] = useState('');
  const [formData, setFormData] = useState({
    batch_id: '',
    feeding_date: '',
    feed_type: '',
    feed_quantity: '',
    feeding_time: '',
    weather: '',
    water_temperature: '',
    notes: ''
  });

  const [signBatch, setSignBatch] = useState('');
  const [signDate, setSignDate] = useState('');
  const [reports, setReports] = useState<DailyFeedingReport[]>([]);
  const [replay, setReplay] = useState<DailyFeedingReport | null>(null);

  const showToast = (kind: string, text: string) => {
    setToast({ kind, text });
    window.setTimeout(() => setToast(null), 4000);
  };

  const fetchFirstPage = async (batchId?: number | '') => {
    setLoading(true);
    try {
      const res = await feedingRecordApi.list({
        limit: PAGE_SIZE,
        ...(batchId ? { batch_id: batchId } : {}),
      });
      setRecords(res.data.items);
      setNextCursor(res.data.next_cursor ?? null);
      setHasMore(res.data.has_more);
    } catch (error) {
      console.error('Error fetching feeding records:', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchAux = async () => {
    try {
      const [batchesRes, conflictsRes, reportsRes] = await Promise.all([
        batchApi.getAll(),
        feedingRecordApi.listConflicts('open'),
        dailyFeedingReportApi.list(),
      ]);
      setBatches(batchesRes.data);
      setConflicts(conflictsRes.data);
      setReports(reportsRes.data);
    } catch (error) {
      console.error('Error fetching aux data:', error);
    }
  };

  useEffect(() => {
    fetchFirstPage(filterBatch);
    fetchAux();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadMore = async () => {
    if (!nextCursor) return;
    setLoadingMore(true);
    try {
      const res = await feedingRecordApi.list({
        limit: PAGE_SIZE,
        cursor: nextCursor,
        ...(filterBatch ? { batch_id: Number(filterBatch) } : {}),
      });
      setRecords(prev => {
        const seen = new Set(prev.map(r => r.id));
        return [...prev, ...res.data.items.filter(r => !seen.has(r.id))];
      });
      setNextCursor(res.data.next_cursor ?? null);
      setHasMore(res.data.has_more);
    } finally {
      setLoadingMore(false);
    }
  };

  const refreshAll = () => {
    fetchFirstPage(filterBatch);
    fetchAux();
  };

  const applyFilter = (value: string) => {
    setFilterBatch(value === '' ? '' : Number(value));
    fetchFirstPage(value === '' ? '' : Number(value));
  };

  const resultMessage = (result: string) => {
    switch (result) {
      case 'created': return { kind: 'success', text: '新投喂已记录' };
      case 'duplicate': return { kind: 'info', text: '重复同步：返回原记录，未重复累计' };
      case 'conflict': return { kind: 'error', text: '同键内容冲突：已挂起，请到“冲突处理”裁决' };
      case 'revised': return { kind: 'success', text: '已生成更正版本' };
      case 'revoked': return { kind: 'success', text: '已生成撤销版本' };
      case 'pending': return { kind: 'warning', text: '批次已关闭，迟报进入待审队列' };
      default: return { kind: 'info', text: result };
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const data: Record<string, unknown> = {
        ...formData,
        batch_id: parseInt(formData.batch_id),
        feed_quantity: parseFloat(formData.feed_quantity),
        water_temperature: formData.water_temperature ? parseFloat(formData.water_temperature) : null,
      };
      if (editingRecord) {
        const res = await feedingRecordApi.correct(
          editingRecord.id, data as Partial<FeedingRecord>,
          effectiveAt || undefined,
        );
        showToast('success', `已生成 v${res.data.record.version} 更正版本`);
      } else {
        const res = await feedingRecordApi.sync(data);
        const msg = resultMessage(res.data.result);
        showToast(msg.kind, msg.text);
      }
      setShowModal(false);
      setEditingRecord(null);
      setEffectiveAt('');
      refreshAll();
    } catch (error: any) {
      showToast('error', error?.response?.data?.detail || '保存失败');
    }
  };

  const handleEdit = (record: FeedingRecord) => {
    setEditingRecord(record);
    setFormData({
      batch_id: record.batch_id.toString(),
      feeding_date: record.feeding_date,
      feed_type: record.feed_type,
      feed_quantity: record.feed_quantity.toString(),
      feeding_time: record.feeding_time || '',
      weather: record.weather || '',
      water_temperature: record.water_temperature?.toString() || '',
      notes: record.notes || ''
    });
    setEffectiveAt('');
    setShowModal(true);
  };

  const handleRevoke = async (record: FeedingRecord) => {
    const when = window.prompt(
      '撤销将生成新版本。\n留空 = 立即生效；\n或输入生效时间 (YYYY-MM-DD HH:MM)：',
      ''
    );
    if (when === null) return;
    try {
      await feedingRecordApi.revoke(record.id, when ? new Date(when).toISOString() : undefined);
      showToast('success', '已撤销，将在生效时点起移出汇总');
      refreshAll();
    } catch (error: any) {
      showToast('error', error?.response?.data?.detail || '撤销失败');
    }
  };

  const handleReview = async (record: FeedingRecord, approve: boolean) => {
    const note = window.prompt(approvePrompt(approve), '') || undefined;
    try {
      if (approve) await feedingRecordApi.approve(record.id, note);
      else await feedingRecordApi.reject(record.id, note);
      showToast('success', approve ? '迟报已批准并计入' : '迟报已驳回');
      refreshAll();
    } catch (error: any) {
      showToast('error', error?.response?.data?.detail || '审批失败');
    }
  };

  const handleResolve = async (c: FeedingConflict, action: 'accept' | 'keep') => {
    const input = window.prompt(
      action === 'accept'
        ? `采纳来报（${c.payload.feed_quantity}kg）将生成更正版本。备注（可空）：`
        : '维持原记录。备注（可空）：',
      ''
    );
    if (input === null) return;
    const note = input.trim() ? input : undefined;
    try {
      await feedingRecordApi.resolveConflict(c.id, action, note);
      showToast('success', action === 'accept' ? '冲突已按来报更正' : '冲突已按原记录维持');
      refreshAll();
    } catch (error: any) {
      showToast('error', error?.response?.data?.detail || '裁决失败');
    }
  };

  const handleSign = async () => {
    if (!signBatch || !signDate) {
      showToast('warning', '请选择批次与业务日期');
      return;
    }
    try {
      const res = await dailyFeedingReportApi.sign(Number(signBatch), signDate);
      showToast('success', `日报已签署：${res.data.total_quantity}kg / ${res.data.feeding_count}次`);
      setReports(prev => [res.data, ...prev.filter(r => r.id !== res.data.id)]);
      fetchAux();
    } catch (error: any) {
      showToast('error', error?.response?.data?.detail || '签署失败（可能已签署）');
    }
  };

  const handleReplay = async (id: number) => {
    try {
      const res = await dailyFeedingReportApi.replay(id);
      setReplay(res.data);
    } catch (error: any) {
      showToast('error', error?.response?.data?.detail || '重放失败');
    }
  };

  const getBatchNumber = (batchId: number) => {
    const batch = batches.find(b => b.id === batchId);
    return batch ? batch.batch_number : `#${batchId}`;
  };

  const badgesFor = (r: FeedingRecord) => {
    const tags: { key: string; meta: { label: string; cls: string; title: string } }[] = [];
    if (r.is_late && r.review_status === 'approved') tags.push({ key: 'late', meta: STATUS_META.late });
    if (r.review_status === 'pending') tags.push({ key: 'pending', meta: STATUS_META.pending });
    if (r.review_status === 'rejected') tags.push({ key: 'rejected', meta: STATUS_META.rejected });
    if (r.has_conflict) tags.push({ key: 'conflict', meta: STATUS_META.conflict });
    if (r.status === 'revoked') tags.push({ key: 'revoked', meta: STATUS_META.revoked });
    if (r.status === 'active' && !r.in_effect && r.review_status === 'approved')
      tags.push({ key: 'future', meta: STATUS_META.future });
    if (r.version > 1 && r.revision_reason === 'correction')
      tags.push({ key: 'revised', meta: { ...STATUS_META.revised, label: `v${r.version} 已更正` } });
    if (r.revision_reason === 'conflict_resolution')
      tags.push({ key: 'revised', meta: { ...STATUS_META.revised, label: `v${r.version} 冲突采纳` } });
    return tags;
  };

  const pendingCount = useMemo(
    () => records.filter(r => r.review_status === 'pending').length,
    [records]
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-500">加载中...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-lg shadow-lg text-sm ${
          toast.kind === 'error' ? 'bg-red-600 text-white'
            : toast.kind === 'warning' ? 'bg-amber-500 text-white'
            : toast.kind === 'info' ? 'bg-ocean-600 text-white'
            : 'bg-green-600 text-white'
        }`}>
          {toast.text}
        </div>
      )}

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">投喂记录</h1>
          <p className="text-gray-600 mt-1">离线补传幂等 · 版本化更正撤销 · 迟报待审</p>
        </div>
        <button
          onClick={() => {
            setEditingRecord(null);
            setEffectiveAt('');
            setFormData({
              batch_id: '', feeding_date: '', feed_type: '', feed_quantity: '',
              feeding_time: '', weather: '', water_temperature: '', notes: ''
            });
            setShowModal(true);
          }}
          className="btn-primary flex items-center space-x-2"
        >
          <Plus size={20} />
          <span>新增记录</span>
        </button>
      </div>

      {/* 待处理提醒 */}
      {(conflicts.length > 0 || pendingCount > 0) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {conflicts.length > 0 && (
            <div className="card border-l-4 border-red-500 bg-red-50">
              <div className="flex items-center space-x-2 text-red-700 font-semibold">
                <AlertTriangle size={18} />
                <span>{conflicts.length} 条同键内容冲突待裁决</span>
              </div>
            </div>
          )}
          {pendingCount > 0 && (
            <div className="card border-l-4 border-amber-500 bg-amber-50">
              <div className="flex items-center space-x-2 text-amber-700 font-semibold">
                <Clock size={18} />
                <span>{pendingCount} 条迟报待审批（当前不计入汇总）</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 冲突处理 */}
      {conflicts.length > 0 && (
        <div className="card">
          <h2 className="text-lg font-semibold text-red-700 mb-3 flex items-center space-x-2">
            <AlertTriangle size={18} /><span>冲突处理</span>
          </h2>
          <div className="space-y-3">
            {conflicts.map(c => (
              <div key={c.id} className="border border-red-200 rounded-lg p-3 bg-white">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-sm">
                    <span className="font-mono text-red-700">{c.device_id}#{c.device_seq}</span>
                    {' '}于 {c.payload.feeding_date} 重传了不同内容：
                    <span className="line-through text-gray-400 ml-2">
                      {c.current_record?.feed_quantity}kg
                    </span>
                    <span className="font-semibold text-red-700 ml-2">
                      来报 {c.payload.feed_quantity}kg · {c.payload.feed_type}
                    </span>
                  </div>
                  <div className="flex space-x-2">
                    <button onClick={() => handleResolve(c, 'accept')}
                      className="px-3 py-1 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700">
                      采纳来报（生成更正版本）
                    </button>
                    <button onClick={() => handleResolve(c, 'keep')}
                      className="px-3 py-1 text-sm btn-secondary">
                      维持原记录
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 游标分页的记录表 */}
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-gray-900">投喂明细（按到达顺序）</h2>
          <select
            value={filterBatch}
            onChange={e => applyFilter(e.target.value)}
            className="select-field max-w-xs"
          >
            <option value="">全部批次</option>
            {batches.map(b => (
              <option key={b.id} value={b.id}>{b.batch_number}</option>
            ))}
          </select>
        </div>
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>批次号</th>
                <th>业务日期</th>
                <th>饲料</th>
                <th>投喂量</th>
                <th>时间/天气</th>
                <th>状态标记</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <tr key={record.id} className={record.status === 'revoked' ? 'opacity-60' : ''}>
                  <td className="font-medium text-ocean-700">
                    {getBatchNumber(record.batch_id)}
                    {record.device_id && (
                      <div className="text-xs text-gray-400 font-mono">
                        {record.device_id}#{record.device_seq} · v{record.version}
                      </div>
                    )}
                  </td>
                  <td>{record.feeding_date}</td>
                  <td>{record.feed_type}</td>
                  <td className={record.status === 'revoked' ? 'line-through' : ''}>
                    {record.status === 'revoked' ? 0 : record.feed_quantity}
                  </td>
                  <td className="text-sm text-gray-500">
                    {record.feeding_time || '-'} {record.weather ? `· ${record.weather}` : ''}
                  </td>
                  <td>
                    <div className="flex flex-wrap gap-1">
                      {badgesFor(record).map(t => (
                        <span key={t.key} title={t.meta.title}
                          className={`px-2 py-0.5 rounded-full text-xs ${t.meta.cls}`}>
                          {t.meta.label}
                        </span>
                      ))}
                      {badgesFor(record).length === 0 && (
                        <span className="text-xs text-gray-400">正常</span>
                      )}
                    </div>
                  </td>
                  <td>
                    <div className="flex items-center space-x-1">
                      {record.review_status === 'pending' && (
                        <>
                          <button onClick={() => handleReview(record, true)}
                            title="批准迟报，计入汇总"
                            className="px-2 py-1 text-xs bg-green-600 text-white rounded-lg">批准</button>
                          <button onClick={() => handleReview(record, false)}
                            title="驳回，不计入"
                            className="px-2 py-1 text-xs bg-gray-300 rounded-lg">驳回</button>
                        </>
                      )}
                      {record.review_status !== 'pending' && record.status !== 'revoked' && (
                        <>
                          <button onClick={() => handleEdit(record)}
                            className="p-2 text-ocean-600 hover:bg-ocean-50 rounded-lg transition-colors">
                            <Edit2 size={16} />
                          </button>
                          <button onClick={() => handleRevoke(record)}
                            className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors">
                            <Ban size={16} />
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
              {records.length === 0 && (
                <tr>
                  <td colSpan={7} className="text-center py-8 text-gray-500">暂无投喂记录</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {hasMore && (
          <div className="flex justify-center mt-4">
            <button onClick={loadMore} disabled={loadingMore} className="btn-secondary">
              {loadingMore ? '加载中...' : '加载更多（游标分页）'}
            </button>
          </div>
        )}
      </div>

      {/* 日报签署与重放 */}
      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-3 flex items-center space-x-2">
          <FileSignature size={18} /><span>日报签署与版本重放</span>
        </h2>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1">批次</label>
            <select value={signBatch} onChange={e => setSignBatch(e.target.value)} className="select-field">
              <option value="">请选择</option>
              {batches.map(b => <option key={b.id} value={b.id}>{b.batch_number}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">业务日期</label>
            <input type="date" value={signDate} onChange={e => setSignDate(e.target.value)}
              className="input-field" />
          </div>
          <button onClick={handleSign} className="btn-primary">按当前版本签署日报</button>
        </div>

        {reports.length > 0 && (
          <div className="mt-4 overflow-x-auto">
            <table className="table text-sm">
              <thead>
                <tr><th>批次</th><th>业务日期</th><th>签署时间</th><th>签署时合计</th><th>操作</th></tr>
              </thead>
              <tbody>
                {reports.map(r => (
                  <tr key={r.id}>
                    <td>{getBatchNumber(r.batch_id)}</td>
                    <td>{r.business_date}</td>
                    <td>{new Date(r.signed_at).toLocaleString()}</td>
                    <td>{r.total_quantity}kg / {r.feeding_count}次</td>
                    <td>
                      <button onClick={() => handleReplay(r.id)}
                        className="flex items-center space-x-1 text-ocean-700 hover:underline">
                        <History size={14} /><span>按当时版本重放</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {replay && (
          <div className="mt-4 p-4 bg-ocean-50 rounded-lg border border-ocean-200">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-ocean-900">
                重放：{replay.business_date} 日报（签署于 {new Date(replay.signed_at).toLocaleString()}）
              </h3>
              <button onClick={() => setReplay(null)} className="text-gray-400 hover:text-gray-600">
                <X size={18} />
              </button>
            </div>
            <p className="text-sm mt-2">
              签署时合计：<strong>{replay.total_quantity}kg / {replay.feeding_count}次</strong>
              {' '}；当前版本合计：{replay.current_totals?.total_quantity}kg / {replay.current_totals?.feeding_count}次
            </p>
            {replay.changed_since_sign ? (
              <p className="text-sm text-amber-700 mt-1">签署后有更正/撤销生效；本重放仍按签署当时版本，未受影响。</p>
            ) : (
              <p className="text-sm text-green-700 mt-1">签署后无变化。</p>
            )}
            <ul className="mt-2 text-sm space-y-1">
              {replay.lines.map(l => (
                <li key={l.record_id} className="flex items-center space-x-2">
                  <span className="font-mono text-xs text-gray-400">v{l.version}</span>
                  <span>{l.feed_type}</span>
                  <span className={l.status === 'revoked' ? 'line-through text-gray-400' : ''}>
                    {l.feed_quantity}kg
                  </span>
                  {l.status === 'revoked' && <span className="badge bg-gray-200 text-gray-600">已撤销</span>}
                  {l.is_late && <span className="badge bg-amber-100 text-amber-800">迟报</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-bold text-gray-900">
                {editingRecord ? `更正投喂记录（将生成 v${editingRecord.version + 1}）` : '新增投喂记录'}
              </h2>
              <button onClick={() => setShowModal(false)} className="p-2 text-gray-400 hover:text-gray-600">
                <X size={20} />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  养殖批次 <span className="text-red-500">*</span>
                </label>
                <select
                  required
                  value={formData.batch_id}
                  onChange={(e) => setFormData({ ...formData, batch_id: e.target.value })}
                  className="select-field"
                >
                  <option value="">请选择批次</option>
                  {batches.map((batch) => (
                    <option key={batch.id} value={batch.id}>
                      {batch.batch_number} - {batch.species}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    业务日期 <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="date"
                    required
                    value={formData.feeding_date}
                    onChange={(e) => setFormData({ ...formData, feeding_date: e.target.value })}
                    className="input-field"
                  />
                  <p className="text-xs text-gray-400 mt-1">跨午夜补传按业务日期归集，与上传时间无关</p>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    饲料类型 <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    required
                    value={formData.feed_type}
                    onChange={(e) => setFormData({ ...formData, feed_type: e.target.value })}
                    className="input-field"
                    placeholder="如: 配合饲料"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    投喂量(公斤) <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="number"
                    step="0.01"
                    required
                    value={formData.feed_quantity}
                    onChange={(e) => setFormData({ ...formData, feed_quantity: e.target.value })}
                    className="input-field"
                    placeholder="投喂量"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">投喂时间</label>
                  <input
                    type="time"
                    value={formData.feeding_time}
                    onChange={(e) => setFormData({ ...formData, feeding_time: e.target.value })}
                    className="input-field"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">天气</label>
                  <input
                    type="text"
                    value={formData.weather}
                    onChange={(e) => setFormData({ ...formData, weather: e.target.value })}
                    className="input-field"
                    placeholder="如: 晴"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">水温(℃)</label>
                  <input
                    type="number"
                    step="0.1"
                    value={formData.water_temperature}
                    onChange={(e) => setFormData({ ...formData, water_temperature: e.target.value })}
                    className="input-field"
                    placeholder="水温"
                  />
                </div>
              </div>

              {editingRecord && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    更正生效时点（留空 = 立即生效）
                  </label>
                  <input
                    type="datetime-local"
                    value={effectiveAt}
                    onChange={e => setEffectiveAt(e.target.value)}
                    className="input-field"
                  />
                  <p className="text-xs text-gray-400 mt-1">
                    已签署日报按签署时点重放，不受此后更正影响
                  </p>
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">备注</label>
                <textarea
                  value={formData.notes}
                  onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
                  className="input-field"
                  rows={3}
                  placeholder="备注信息"
                />
              </div>

              <div className="flex justify-end space-x-3 pt-4">
                <button type="button" onClick={() => setShowModal(false)} className="btn-secondary">
                  取消
                </button>
                <button type="submit" className="btn-primary">
                  {editingRecord ? '提交更正（新版本）' : '创建'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

// 审批提示语（单独抽出避免与上方 JSX 混淆）
function approvePrompt(approve: boolean) {
  return approve ? '批准该迟报，审批后计入汇总。备注（可空）：' : '驳回该迟报，不计入汇总。备注（可空）：';
}

export default FeedingRecords;
