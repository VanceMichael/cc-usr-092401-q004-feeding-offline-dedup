import axios from 'axios';
import type {
  Pond, Batch, StockingRecord, FeedingRecord, FeedingSyncResult, FeedingPage,
  FeedingConflict, DailyFeedingReport, WaterQualityRecord,
  MedicationRecord, CostRecord, HarvestSale, CultureCycleAnalysis,
  CostSummary, FeedingSummary, BatchTraceability
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

export interface FeedingListParams {
  limit?: number;
  cursor?: string | null;
  batch_id?: number;
  business_date?: string;
  review_status?: string;
  include_history?: boolean;
}

export const feedingRecordApi = {
  list: (params: FeedingListParams = {}) =>
    api.get<FeedingPage>('/feeding-records/', { params }),
  // 兼容旧调用：一次性取首页
  getAll: (batchId?: number) =>
    api.get<FeedingPage>('/feeding-records/', {
      params: { limit: 200, ...(batchId ? { batch_id: batchId } : {}) },
    }),
  getById: (id: number) => api.get<FeedingRecord>(`/feeding-records/${id}/`),
  // 设备/人工上送，返回 result 区分 created/duplicate/conflict/revised/revoked/pending
  sync: (data: Record<string, unknown>) =>
    api.post<FeedingSyncResult>('/feeding-records/', data),
  // 兼容旧调用
  create: (data: Record<string, unknown>) =>
    api.post<FeedingSyncResult>('/feeding-records/', data),
  // 人工更正（新版本，可指定生效时点）
  correct: (id: number, data: Partial<FeedingRecord>, effectiveAt?: string) =>
    api.put<FeedingSyncResult>(`/feeding-records/${id}/`, data, {
      params: effectiveAt ? { effective_at: effectiveAt } : {},
    }),
  update: (id: number, data: Partial<FeedingRecord>) =>
    api.put<FeedingSyncResult>(`/feeding-records/${id}/`, data),
  // 撤销（新版本）
  revoke: (id: number, effectiveAt?: string) =>
    api.delete<{ message: string; record: FeedingRecord }>(`/feeding-records/${id}/`, {
      params: effectiveAt ? { effective_at: effectiveAt } : {},
    }),
  delete: (id: number) => api.delete(`/feeding-records/${id}/`),
  approve: (id: number, note?: string) =>
    api.post(`/feeding-records/${id}/approve/`, null, { params: { note } }),
  reject: (id: number, note?: string) =>
    api.post(`/feeding-records/${id}/reject/`, null, { params: { note } }),
  listConflicts: (status = 'open') =>
    api.get<FeedingConflict[]>('/feeding-records/conflicts/', { params: { status } }),
  resolveConflict: (conflictId: number, action: 'accept' | 'keep', note?: string) =>
    api.post(`/feeding-records/conflicts/${conflictId}/resolve/`, null, {
      params: { action, note },
    }),
  dailyTotals: (batchId: number, businessDate: string, asOf?: string) =>
    api.get('/feeding-records/daily-totals/', {
      params: { batch_id: batchId, business_date: businessDate, as_of: asOf },
    }),
};

export const dailyFeedingReportApi = {
  sign: (batchId: number, businessDate: string, asOf?: string) =>
    api.post<DailyFeedingReport>('/feeding-daily-reports/sign/', null, {
      params: { batch_id: batchId, business_date: businessDate, as_of: asOf },
    }),
  list: (batchId?: number) =>
    api.get<DailyFeedingReport[]>('/feeding-daily-reports/', {
      params: batchId ? { batch_id: batchId } : {},
    }),
  replay: (reportId: number) =>
    api.get<DailyFeedingReport>(`/feeding-daily-reports/${reportId}/replay/`),
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
  analyzeCycle: (batchId: number) => 
    api.get<CultureCycleAnalysis>(`/analysis/cycle/${batchId}/`),
  batchTraceability: (batchId: number) => 
    api.get<BatchTraceability>(`/analysis/traceability/${batchId}/`),
  traceByBatchNumber: (batchNumber: string) => 
    api.get<BatchTraceability>(`/analysis/trace-by-number/${batchNumber}/`),
};

export default api;
