export interface OnboardedMerchant {
  merchant_id: string;
  name: string;
  category: string | null;
  monthly_gmv_paise: number | null;
  created_at: string | null;
  data_source: "merchant_csv" | string;
  rows_imported: number;
  historical_observations: number;
  real_observations: number;
  simulated_observations: number;
  segment_count: number;
}

export interface MerchantDataStatus {
  merchant_id: string;
  data_source: string;
  historical_observations: number;
  real_observations: number;
  simulated_observations: number;
  segment_count: number;
  has_data: boolean;
}

export interface DemoMerchantSource {
  merchant_id: string;
  name: string;
  data_source: "demo" | string;
  historical_observations: number;
  segment_count: number;
}
