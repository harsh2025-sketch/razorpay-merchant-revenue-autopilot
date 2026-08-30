export interface OneClickExperimentResult {
  experiment_id: string;
  generated_attempts: number;
  runtime_batches: number;
  control_attempts: number;
  treatment_attempts: number;
  sample_target_per_variant: number;
  decision: "KEEP" | "ROLLBACK" | "INCONCLUSIVE";
  absolute_lift: number | null;
  p_value: number | null;
}
