import axios from 'axios';
import type {
  Pond, Batch, StockingRecord, FeedingRecord, WaterQualityRecord,
  MedicationRecord, CostRecord, HarvestSale, CultureCycleAnalysis,
  CostSummary, FeedingSummary, BatchTraceability,
  FeedingRecordPage, FeedingSyncResult, FeedingConflict, DailyReport
} from '../types';

const API_BASE_URL = '/api';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const pondApi = {
  getAll: () => api.get<Pond[]>('/ponds/'),
  getById: (id: number) => api.get<Pond>(`/ponds/${id}/`),
  create: (data: Omit<Pond, 'id' | 'created_at' | 'updated_at'>) => 
    api.post<Pond>('/ponds/', data),
  update: (id: number, data: Partial<Pond>) => 
    api.put<Pond>(`/ponds/${id}/`, data),
  delete: (id: number) => api.delete(`/ponds/${id}/`),
};

export const batchApi = {
  getAll: () => api.get<Batch[]>('/batches/'),
  getById: (id: number) => api.get<Batch>(`/batches/${id}/`),
  getByNumber: (batchNumber: string) => 
    api.get<Batch>(`/batches/by-number/${batchNumber}/`),
  create: (data: Omit<Batch, 'id' | 'created_at' | 'updated_at'>) => 
    api.post<Batch>('/batches/', data),
  update: (id: number, data: Partial<Batch>) => 
    api.put<Batch>(`/batches/${id}/`, data),
  delete: (id: number) => api.delete(`/batches/${id}/`),
};

export const stockingRecordApi = {
  getAll: (batchId?: number) => 
    api.get<StockingRecord[]>('/stocking-records/', { 
      params: batchId ? { batch_id: batchId } : {} 
    }),
  getById: (id: number) => api.get<StockingRecord>(`/stocking-records/${id}/`),
  create: (data: Omit<StockingRecord, 'id' | 'created_at'>) => 
    api.post<StockingRecord>('/stocking-records/', data),
  update: (id: number, data: Partial<StockingRecord>) => 
    api.put<StockingRecord>(`/stocking-records/${id}/`, data),
  delete: (id: number) => api.delete(`/stocking-records/${id}/`),
};

export interface FeedingPageParams {
  limit?: number;
  cursor?: string;
  batchId?: number;
  reviewStatus?: string;
}

export const feedingRecordApi = {
  getPage: (params: FeedingPageParams = {}) =>
    api.get<FeedingRecordPage>('/feeding-records/', {
      params: {
        limit: params.limit ?? 50,
        cursor: params.cursor || undefined,
        batch_id: params.batchId || undefined,
        review_status: params.reviewStatus || undefined,
      },
    }),
  getAll: async (batchId?: number): Promise<{ data: FeedingRecord[] }> => {
    // 兼容旧调用方:自动翻完所有游标分页
    let cursor: string | undefined;
    const all: FeedingRecord[] = [];
    for (;;) {
      const res = await api.get<FeedingRecordPage>('/feeding-records/', {
        params: {
          limit: 200,
          cursor: cursor || undefined,
          batch_id: batchId || undefined,
        },
      });
      all.push(...res.data.items);
      if (!res.data.has_more || !res.data.next_cursor) break;
      cursor = res.data.next_cursor;
    }
    return { data: all };
  },
  getById: (id: number) => api.get<FeedingRecord>(`/feeding-records/${id}/`),
  // 统一同步入口:重复返回原记录(200),冲突 409,更正/撤销为新版本
  sync: (data: Record<string, unknown>) =>
    api.post<FeedingSyncResult>('/feeding-records/', data),
  create: (data: Record<string, unknown>) =>
    api.post<FeedingSyncResult>('/feeding-records/', data),
  revise: (rootId: number, data: Record<string, unknown>) =>
    api.post<FeedingRecord>('/feeding-records/', { ...data, root_id: rootId, action: 'upsert' }),
  revoke: (id: number, effectiveAt?: string) =>
    api.post<FeedingSyncResult>(`/feeding-records/${id}/revoke/`,
      effectiveAt ? { effective_at: effectiveAt } : {}),
  review: (id: number, action: 'approve' | 'reject') =>
    api.post<FeedingRecord>(`/feeding-records/${id}/review/`, { action }),
  listConflicts: () =>
    api.get<FeedingConflict[]>('/feeding-records/conflicts/list/'),
  resolveConflict: (conflictId: number, resolution: 'kept' | 'applied') =>
    api.post<FeedingConflict>(`/feeding-records/conflicts/${conflictId}/resolve/`, {
      action: 'resolve_conflict',
      resolution,
    }),
};

export const waterQualityRecordApi = {
  getAll: (batchId?: number) => 
    api.get<WaterQualityRecord[]>('/water-quality-records/', { 
      params: batchId ? { batch_id: batchId } : {} 
    }),
  getById: (id: number) => api.get<WaterQualityRecord>(`/water-quality-records/${id}/`),
  create: (data: Omit<WaterQualityRecord, 'id' | 'created_at'>) => 
    api.post<WaterQualityRecord>('/water-quality-records/', data),
  update: (id: number, data: Partial<WaterQualityRecord>) => 
    api.put<WaterQualityRecord>(`/water-quality-records/${id}/`, data),
  delete: (id: number) => api.delete(`/water-quality-records/${id}/`),
};

export const medicationRecordApi = {
  getAll: (batchId?: number) => 
    api.get<MedicationRecord[]>('/medication-records/', { 
      params: batchId ? { batch_id: batchId } : {} 
    }),
  getById: (id: number) => api.get<MedicationRecord>(`/medication-records/${id}/`),
  create: (data: Omit<MedicationRecord, 'id' | 'created_at'>) => 
    api.post<MedicationRecord>('/medication-records/', data),
  update: (id: number, data: Partial<MedicationRecord>) => 
    api.put<MedicationRecord>(`/medication-records/${id}/`, data),
  delete: (id: number) => api.delete(`/medication-records/${id}/`),
};

export const costRecordApi = {
  getAll: (batchId?: number, costType?: string) => 
    api.get<CostRecord[]>('/cost-records/', { 
      params: { batch_id: batchId, cost_type: costType } 
    }),
  getById: (id: number) => api.get<CostRecord>(`/cost-records/${id}/`),
  create: (data: Omit<CostRecord, 'id' | 'created_at'>) => 
    api.post<CostRecord>('/cost-records/', data),
  update: (id: number, data: Partial<CostRecord>) => 
    api.put<CostRecord>(`/cost-records/${id}/`, data),
  delete: (id: number) => api.delete(`/cost-records/${id}/`),
};

export const harvestSaleApi = {
  getAll: (batchId?: number) => 
    api.get<HarvestSale[]>('/harvest-sales/', { 
      params: batchId ? { batch_id: batchId } : {} 
    }),
  getById: (id: number) => api.get<HarvestSale>(`/harvest-sales/${id}/`),
  create: (data: Omit<HarvestSale, 'id' | 'created_at'>) => 
    api.post<HarvestSale>('/harvest-sales/', data),
  update: (id: number, data: Partial<HarvestSale>) => 
    api.put<HarvestSale>(`/harvest-sales/${id}/`, data),
  delete: (id: number) => api.delete(`/harvest-sales/${id}/`),
};

export const analysisApi = {
  analyzeCycle: (batchId: number, asOf?: string) =>
    api.get<CultureCycleAnalysis>(`/analysis/cycle/${batchId}/`, {
      params: asOf ? { as_of: asOf } : {},
    }),
  batchTraceability: (batchId: number, asOf?: string) =>
    api.get<BatchTraceability>(`/analysis/traceability/${batchId}/`, {
      params: asOf ? { as_of: asOf } : {},
    }),
  traceByBatchNumber: (batchNumber: string, asOf?: string) =>
    api.get<BatchTraceability>(`/analysis/trace-by-number/${batchNumber}/`, {
      params: asOf ? { as_of: asOf } : {},
    }),
};

export const feedingReportApi = {
  sign: (batchId: number, businessDate: string) =>
    api.post<DailyReport>(`/feeding-reports/${batchId}/${businessDate}/`),
  get: (batchId: number, businessDate: string) =>
    api.get<DailyReport>(`/feeding-reports/${batchId}/${businessDate}/`),
};

export default api;
