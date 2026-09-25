export interface Pond {
  id: number;
  name: string;
  area: number;
  water_depth: number;
  species?: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface Batch {
  id: number;
  batch_number: string;
  pond_id: number;
  species: string;
  stocking_date: string;
  estimated_harvest_date?: string;
  actual_harvest_date?: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface StockingRecord {
  id: number;
  batch_id: number;
  species: string;
  quantity: number;
  source?: string;
  batch_number?: string;
  weight_per_unit?: number;
  total_weight?: number;
  notes?: string;
  created_at: string;
}

export interface FeedingRecord {
  id: number;
  batch_id: number;
  feeding_date: string;
  feed_type: string;
  feed_quantity: number;
  feeding_time?: string;
  weather?: string;
  water_temperature?: number;
  notes?: string;
  created_at: string;
  device_id?: string | null;
  client_seq?: string | null;
  content_hash?: string | null;
  version: number;
  action: 'upsert' | 'revoke';
  root_id?: number | null;
  replaces_id?: number | null;
  occurred_at?: string | null;
  effective_at: string;
  received_at: string;
  review_status: 'approved' | 'pending' | 'rejected';
  pending_reason?: 'late_closed' | 'late_window' | null;
  conflict_flag: boolean;
  is_late?: boolean;
  is_revoked?: boolean;
  has_newer_version?: boolean;
}

export interface FeedingSyncResult {
  outcome: 'created' | 'duplicate' | 'conflict' | 'revised' | 'revoked' | 'pending';
  message: string;
  record: FeedingRecord;
  conflict_id?: number | null;
}

export interface FeedingRecordPage {
  items: FeedingRecord[];
  next_cursor?: string | null;
  has_more: boolean;
}

export interface FeedingConflict {
  id: number;
  device_id: string;
  client_seq: string;
  version: number;
  batch_id?: number | null;
  existing_record_id?: number | null;
  incoming_payload?: string | null;
  resolved: boolean;
  resolution?: string | null;
  created_at: string;
}

export interface DailyReport {
  id: number;
  batch_id: number;
  business_date: string;
  signed_at: string;
  as_of: string;
  total_quantity: number;
  record_count: number;
  snapshot: FeedingRecord[];
}

export interface WaterQualityRecord {
  id: number;
  batch_id: number;
  record_date: string;
  record_time?: string;
  water_temperature?: number;
  ph_value?: number;
  dissolved_oxygen?: number;
  ammonia_nitrogen?: number;
  nitrite?: number;
  transparency?: number;
  notes?: string;
  created_at: string;
}

export interface MedicationRecord {
  id: number;
  batch_id: number;
  medication_date: string;
  drug_name: string;
  drug_type?: string;
  dosage?: number;
  dosage_unit: string;
  administration_method?: string;
  purpose?: string;
  manufacturer?: string;
  batch_number?: string;
  notes?: string;
  created_at: string;
}

export interface CostRecord {
  id: number;
  batch_id: number;
  cost_date: string;
  cost_type: string;
  amount: number;
  description?: string;
  quantity?: number;
  unit?: string;
  unit_price?: number;
  notes?: string;
  created_at: string;
}

export interface HarvestSale {
  id: number;
  batch_id: number;
  sale_date: string;
  weight: number;
  unit_price: number;
  total_amount?: number;
  buyer?: string;
  batch_number?: string;
  quality_grade?: string;
  notes?: string;
  created_at: string;
}

export interface CostSummary {
  feed_cost: number;
  medicine_cost: number;
  labor_cost: number;
  electricity_cost: number;
  other_cost: number;
  total_cost: number;
}

export interface FeedingSummary {
  total_feed_weight: number;
  feeding_count: number;
  avg_daily_feed: number;
  by_feed_type?: Record<string, { total_quantity: number; feeding_count: number }>;
}

export interface CultureCycleAnalysis {
  batch_number: string;
  pond_name: string;
  species: string;
  stocking_date: string;
  harvest_date?: string;
  days_cultured?: number;
  initial_quantity: number;
  harvest_weight: number;
  survival_rate: number;
  feed_total: number;
  feed_conversion_ratio: number;
  area: number;
  yield_per_mu: number;
  total_cost: number;
  total_revenue: number;
  profit: number;
  cost_summary?: CostSummary;
  feeding_summary?: FeedingSummary;
}

export interface ApiResponse<T> {
  data?: T;
  message?: string;
}

export interface StockingRecordTrace {
  species: string;
  quantity: number;
  source?: string;
  batch_number?: string;
  stocking_date?: string;
}

export interface FeedingRecordTrace {
  feeding_date: string;
  feed_type: string;
  quantity: number;
  unit?: string;
  version?: number;
  action?: string;
  review_status?: string;
  is_late?: boolean;
  conflict_flag?: boolean;
  effective_at?: string | null;
}

export interface WaterQualityRecordTrace {
  record_date: string;
  water_temperature?: number;
  ph_value?: number;
  dissolved_oxygen?: number;
}

export interface MedicationRecordTrace {
  medication_date: string;
  medication_name: string;
  dosage?: number;
  unit?: string;
}

export interface CostRecordTrace {
  cost_date: string;
  cost_type: string;
  amount: number;
  description?: string;
}

export interface HarvestSaleTrace {
  sale_date: string;
  weight: number;
  unit_price: number;
  total_amount?: number;
  buyer?: string;
}

export interface BatchInfo {
  batch_number: string;
  species: string;
  stocking_date: string;
  harvest_date?: string;
  status: string;
  pond_id?: number;
}

export interface PondInfo {
  name?: string;
  area?: number;
  water_depth?: number;
}

export interface BatchTraceability {
  batch: BatchInfo;
  pond_info: PondInfo;
  stocking_records: StockingRecordTrace[];
  feeding_records: FeedingRecordTrace[];
  water_quality_records: WaterQualityRecordTrace[];
  medication_records: MedicationRecordTrace[];
  cost_records: CostRecordTrace[];
  harvest_sales: HarvestSaleTrace[];
}
