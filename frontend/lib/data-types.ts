export interface IncrementalCsvResult {
  merchant_id: string;
  data_source: string;
  rows_received: number;
  rows_appended: number;
  rows_deduplicated: number;
  historical_observations: number;
  real_observations: number;
  simulated_observations: number;
  segment_count: number;
}

export interface DemoPeriodResult {
  merchant_id: string;
  data_source: string;
  period_index: number;
  period_start: string;
  period_end: string;
  rows_appended: number;
  historical_observations: number;
  real_observations: number;
  simulated_observations: number;
  segment_count: number;
}
