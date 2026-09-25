import React, { useEffect, useState, useCallback } from 'react';
import { Plus, Edit2, Trash2, X, Check, Ban, RefreshCw, AlertTriangle, Clock, FileWarning, History } from 'lucide-react';
import axios from 'axios';
import { feedingRecordApi, batchApi } from '../services/api';
import type { FeedingRecord, Batch, FeedingSyncResult, FeedingConflict } from '../types';

const PAGE_SIZE = 20;

const FeedingRecords: React.FC = () => {
  const [records, setRecords] = useState<FeedingRecord[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [editingRecord, setEditingRecord] = useState<FeedingRecord | null>(null);
  const [feedback, setFeedback] = useState<{ type: 'info' | 'success' | 'warning' | 'error'; text: string } | null>(null);
  const [statusFilter, setStatusFilter] = useState<'all' | 'pending'>('all');
  const [conflicts, setConflicts] = useState<FeedingConflict[]>([]);
  const [formData, setFormData] = useState({
    batch_id: '',
    feeding_date: '',
    feed_type: '',
    feed_quantity: '',
    feeding_time: '',
    weather: '',
    water_temperature: '',
    notes: '',
    device_id: '',
    client_seq: '',
    occurred_at: '',
  });

  const showFeedback = (type: 'info' | 'success' | 'warning' | 'error', text: string) => {
    setFeedback({ type, text });
    window.setTimeout(() => setFeedback(null), 6000);
  };

  const fetchFirstPage = useCallback(async (status: 'all' | 'pending' = statusFilter) => {
    setLoading(true);
    try {
      const [recordsRes, batchesRes] = await Promise.all([
        feedingRecordApi.getPage({
          limit: PAGE_SIZE,
          reviewStatus: status === 'pending' ? 'pending' : undefined,
        }),
        batchApi.getAll(),
      ]);
      setRecords(recordsRes.data.items);
      setCursor(recordsRes.data.next_cursor ?? null);
      setHasMore(recordsRes.data.has_more);
      setBatches(batchesRes.data);
    } catch (error) {
      console.error('Error fetching data:', error);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    fetchFirstPage(statusFilter);
    fetchConflicts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  const fetchConflicts = async () => {
    try {
      const res = await feedingRecordApi.listConflicts();
      setConflicts(res.data);
    } catch (err) {
      console.error('Error fetching conflicts:', err);
    }
  };

  const handleResolveConflict = async (conflict: FeedingConflict, resolution: 'kept' | 'applied') => {
    const word = resolution === 'kept' ? '保留原记录' : '采用新内容(生成新版本)';
    if (!window.confirm(`确定对该冲突执行「${word}」吗？`)) return;
    try {
      await feedingRecordApi.resolveConflict(conflict.id, resolution);
      showFeedback('success', `冲突已处理:${word}`);
      await Promise.all([fetchConflicts(), fetchFirstPage(statusFilter)]);
    } catch (err) {
      console.error('Error resolving conflict:', err);
      showFeedback('error', '冲突处理失败');
    }
  };

  const loadMore = async () => {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const res = await feedingRecordApi.getPage({
        limit: PAGE_SIZE,
        cursor,
        reviewStatus: statusFilter === 'pending' ? 'pending' : undefined,
      });
      // 键集游标本身保证不重,这里再按 id 去一次重做双保险
      setRecords(prev => {
        const seen = new Set(prev.map(r => r.id));
        return [...prev, ...res.data.items.filter(r => !seen.has(r.id))];
      });
      setCursor(res.data.next_cursor ?? null);
      setHasMore(res.data.has_more);
    } finally {
      setLoadingMore(false);
    }
  };

  const resetForm = () => setFormData({
    batch_id: '', feeding_date: '', feed_type: '', feed_quantity: '',
    feeding_time: '', weather: '', water_temperature: '', notes: '',
    device_id: '', client_seq: '', occurred_at: '',
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const data: Record<string, unknown> = {
      batch_id: parseInt(formData.batch_id),
      feeding_date: formData.feeding_date,
      feed_type: formData.feed_type,
      feed_quantity: parseFloat(formData.feed_quantity),
      feeding_time: formData.feeding_time || undefined,
      weather: formData.weather || undefined,
      water_temperature: formData.water_temperature ? parseFloat(formData.water_temperature) : undefined,
      notes: formData.notes || undefined,
      device_id: formData.device_id || undefined,
      client_seq: formData.client_seq || undefined,
      occurred_at: formData.occurred_at ? new Date(formData.occurred_at).toISOString() : undefined,
    };
    if (editingRecord) {
      // 更正 = 同一逻辑记录的新版本,不覆盖历史
      data.root_id = editingRecord.root_id || editingRecord.id;
      data.action = 'upsert';
    }
    try {
      const res = await feedingRecordApi.sync(data);
      const result = res.data as FeedingSyncResult;
      const tone = {
        created: 'success', duplicate: 'info', conflict: 'warning',
        revised: 'success', revoked: 'success', pending: 'warning',
      }[result.outcome] as 'success' | 'info' | 'warning';
      showFeedback(tone, result.message);
      setShowModal(false);
      setEditingRecord(null);
      resetForm();
      fetchFirstPage(statusFilter);
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        const body = err.response.data as FeedingSyncResult;
        showFeedback('warning', `${body.message}（冲突编号 #${body.conflict_id}，已保留原记录）`);
      } else {
        showFeedback('error', '保存失败,请检查输入');
        console.error('Error saving record:', err);
      }
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
      notes: record.notes || '',
      device_id: record.device_id || '',
      client_seq: record.client_seq || '',
      occurred_at: record.occurred_at ? new Date(record.occurred_at).toISOString().slice(0, 16) : '',
    });
    setShowModal(true);
  };

  const handleRevoke = async (record: FeedingRecord) => {
    if (!window.confirm('确定撤销这条投喂记录吗？撤销以新版本生效,历史与已签日报不受影响。')) return;
    try {
      const res = await feedingRecordApi.revoke(record.root_id || record.id);
      showFeedback('success', res.data.message);
      fetchFirstPage(statusFilter);
    } catch (error) {
      console.error('Error revoking record:', error);
    }
  };

  const handleReview = async (record: FeedingRecord, action: 'approve' | 'reject') => {
    try {
      await feedingRecordApi.review(record.id, action);
      showFeedback('success', action === 'approve' ? '已批准,记录即刻计入汇总' : '已驳回');
      fetchFirstPage(statusFilter);
    } catch (error) {
      console.error('Error reviewing record:', error);
    }
  };

  const getBatchNumber = (batchId: number) =>
    batches.find(b => b.id === batchId)?.batch_number ?? '未知批次';

  const badge = (record: FeedingRecord) => {
    const items: { text: string; cls: string; icon: React.ReactNode }[] = [];
    if (record.review_status === 'pending') {
      items.push({
        text: record.pending_reason === 'late_closed' ? '关闭后迟报·待审' : '迟报·待审',
        cls: 'bg-amber-100 text-amber-800 border border-amber-300',
        icon: <Clock size={12} />,
      });
    } else if (record.is_late) {
      items.push({ text: '迟报', cls: 'bg-yellow-100 text-yellow-800 border border-yellow-300', icon: <Clock size={12} /> });
    }
    if (record.conflict_flag) {
      items.push({ text: '冲突', cls: 'bg-red-100 text-red-800 border border-red-300', icon: <FileWarning size={12} /> });
    }
    if (record.is_revoked) {
      items.push({ text: '已撤销', cls: 'bg-gray-200 text-gray-600 border border-gray-400 line-through', icon: <Ban size={12} /> });
    } else if (record.has_newer_version) {
      items.push({ text: '历史版本', cls: 'bg-blue-100 text-blue-700 border border-blue-300', icon: <History size={12} /> });
    }
    if (record.version > 1 && !record.is_revoked && !record.has_newer_version) {
      items.push({ text: `v${record.version} 已更正`, cls: 'bg-indigo-50 text-indigo-700 border border-indigo-200', icon: <RefreshCw size={12} /> });
    }
    return (
      <div className="flex flex-wrap gap-1">
        {items.map((b, i) => (
          <span key={i} className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs ${b.cls}`}>
            {b.icon}{b.text}
          </span>
        ))}
      </div>
    );
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-500">加载中...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">投喂记录</h1>
          <p className="text-gray-600 mt-1">离线补传自动去重,重复同步返回原记录;更正/撤销以版本方式生效</p>
        </div>
        <button
          onClick={() => { setEditingRecord(null); resetForm(); setShowModal(true); }}
          className="btn-primary flex items-center space-x-2"
        >
          <Plus size={20} />
          <span>新增记录</span>
        </button>
      </div>

      {feedback && (
        <div className={`p-3 rounded-lg border text-sm flex items-start gap-2 ${
          feedback.type === 'error' ? 'bg-red-50 border-red-300 text-red-800' :
          feedback.type === 'warning' ? 'bg-amber-50 border-amber-300 text-amber-900' :
          feedback.type === 'success' ? 'bg-green-50 border-green-300 text-green-800' :
          'bg-blue-50 border-blue-300 text-blue-800'
        }`}>
          {feedback.type === 'warning' || feedback.type === 'error'
            ? <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            : <Check size={16} className="mt-0.5 shrink-0" />}
          <span>{feedback.text}</span>
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          onClick={() => setStatusFilter('all')}
          className={`px-3 py-1.5 rounded-lg text-sm border ${statusFilter === 'all' ? 'bg-ocean-600 text-white border-ocean-600' : 'bg-white text-gray-600 border-gray-300'}`}
        >
          全部记录
        </button>
        <button
          onClick={() => setStatusFilter('pending')}
          className={`px-3 py-1.5 rounded-lg text-sm border ${statusFilter === 'pending' ? 'bg-amber-500 text-white border-amber-500' : 'bg-white text-gray-600 border-gray-300'}`}
        >
          待审队列
        </button>
      </div>

      {conflicts.length > 0 && (
        <div className="card border-2 border-red-300 bg-red-50">
          <h2 className="text-lg font-semibold text-red-800 mb-3 flex items-center gap-2">
            <FileWarning size={20} />
            同键内容冲突待处理 ({conflicts.length})
          </h2>
          <p className="text-sm text-red-700 mb-3">
            同一设备号+流水号(+版本)到达了内容不一致的同步。系统已保留原记录、未覆盖、未重复累计,请人工裁定:
          </p>
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>冲突编号</th>
                  <th>设备 / 流水号</th>
                  <th>版本</th>
                  <th>批次</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {conflicts.map(c => (
                  <tr key={c.id}>
                    <td className="font-medium">#{c.id}</td>
                    <td>{c.device_id}#{c.client_seq}</td>
                    <td>v{c.version}</td>
                    <td>{c.batch_id ? getBatchNumber(c.batch_id) : '-'}</td>
                    <td>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => handleResolveConflict(c, 'kept')}
                          className="px-3 py-1 text-sm rounded-lg border border-gray-300 bg-white hover:bg-gray-100"
                        >
                          保留原记录
                        </button>
                        <button
                          onClick={() => handleResolveConflict(c, 'applied')}
                          className="px-3 py-1 text-sm rounded-lg border border-red-400 bg-red-600 text-white hover:bg-red-700"
                        >
                          采用新内容
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card">
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>批次号</th>
                <th>投喂日期</th>
                <th>饲料类型</th>
                <th>投喂量(公斤)</th>
                <th>状态标记</th>
                <th>版本/设备</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <tr key={record.id} className={record.is_revoked ? 'opacity-60' : ''}>
                  <td className="font-medium text-ocean-700">{getBatchNumber(record.batch_id)}</td>
                  <td>{record.feeding_date}</td>
                  <td>{record.feed_type}</td>
                  <td className={record.is_revoked ? 'line-through text-gray-400' : ''}>
                    {record.feed_quantity}
                  </td>
                  <td>{badge(record)}</td>
                  <td className="text-xs text-gray-500">
                    <div>v{record.version}{record.action === 'revoke' ? ' 撤销' : ''}</div>
                    {record.device_id && <div title="设备号/流水号">{record.device_id}#{record.client_seq}</div>}
                  </td>
                  <td>
                    <div className="flex items-center space-x-2">
                      {record.review_status === 'pending' ? (
                        <>
                          <button
                            title="批准(即刻计入汇总)"
                            onClick={() => handleReview(record, 'approve')}
                            className="p-2 text-green-600 hover:bg-green-50 rounded-lg transition-colors"
                          >
                            <Check size={18} />
                          </button>
                          <button
                            title="驳回"
                            onClick={() => handleReview(record, 'reject')}
                            className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                          >
                            <Ban size={18} />
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            title="更正(产生新版本)"
                            onClick={() => handleEdit(record)}
                            className="p-2 text-ocean-600 hover:bg-ocean-50 rounded-lg transition-colors"
                            disabled={record.is_revoked}
                          >
                            <Edit2 size={18} />
                          </button>
                          <button
                            title="撤销(新版本,保留历史)"
                            onClick={() => handleRevoke(record)}
                            className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                            disabled={record.is_revoked}
                          >
                            <Trash2 size={18} />
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
              {records.length === 0 && (
                <tr>
                  <td colSpan={7} className="text-center py-8 text-gray-500">
                    {statusFilter === 'pending' ? '暂无待审记录' : '暂无投喂记录'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {hasMore && (
          <div className="flex justify-center py-4">
            <button
              onClick={loadMore}
              disabled={loadingMore}
              className="btn-secondary flex items-center gap-2"
            >
              <RefreshCw size={16} className={loadingMore ? 'animate-spin' : ''} />
              {loadingMore ? '加载中...' : '加载更多'}
            </button>
          </div>
        )}
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-bold text-gray-900">
                {editingRecord ? `更正投喂记录(生成 v${(editingRecord.version || 1) + 1} 新版本)` : '新增投喂记录'}
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
                    投喂日期(业务日期) <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="date"
                    required
                    value={formData.feeding_date}
                    onChange={(e) => setFormData({ ...formData, feeding_date: e.target.value })}
                    className="input-field"
                  />
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

              <div className="border-t pt-4">
                <p className="text-sm font-medium text-gray-700 mb-2">离线设备信息(补传去重用)</p>
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">设备号</label>
                    <input
                      type="text"
                      value={formData.device_id}
                      onChange={(e) => setFormData({ ...formData, device_id: e.target.value })}
                      className="input-field"
                      placeholder="DEV-01"
                      disabled={!!editingRecord}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">本地流水号</label>
                    <input
                      type="text"
                      value={formData.client_seq}
                      onChange={(e) => setFormData({ ...formData, client_seq: e.target.value })}
                      className="input-field"
                      placeholder="1024"
                      disabled={!!editingRecord}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">事件发生时间</label>
                    <input
                      type="datetime-local"
                      value={formData.occurred_at}
                      onChange={(e) => setFormData({ ...formData, occurred_at: e.target.value })}
                      className="input-field"
                    />
                  </div>
                </div>
                {editingRecord && (
                  <p className="text-xs text-gray-400 mt-2">
                    更正将追加新版本并从提交时起影响汇总;同设备同流水号重发原内容不会重复累计。
                  </p>
                )}
              </div>

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
                  {editingRecord ? '提交更正(新版本)' : '创建'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default FeedingRecords;
